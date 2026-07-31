"""content_sources/ — the interface, factory selection, and providers of
the Content Rewards Connector. ContentRewardsProvider is tested against
mocked HTTP responses only, never a live network call (same philosophy as
test_publishing_providers.py) — see
content_sources/providers/content_rewards_provider.py's module docstring
for what was actually captured from the real site and why this provider
only covers campaigns with a public Google Drive link in their
description."""

import json

import httpx
import pytest

from content_factory.config import Settings
from content_factory.content_sources.base import RemoteCampaignVideo
from content_factory.content_sources.factory import get_content_source_provider
from content_factory.content_sources.providers.content_rewards_provider import ContentRewardsProvider
from content_factory.content_sources.providers.manual_provider import ManualContentSourceProvider
from content_factory.retry import ProviderRequestRejected, RetryableProviderError


def _video(external_id: str = "x") -> RemoteCampaignVideo:
    return RemoteCampaignVideo(
        external_id=external_id, title="t", campaign_name="c", duration_s=None,
        download_url="", source_page_url="",
    )


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", json_body: dict | None = None, content: bytes = b""):
        self.status_code = status_code
        self.text = text
        self._json_body = json_body if json_body is not None else {}
        self.content = content

    def json(self) -> dict:
        return self._json_body


class _FakeStreamResponse:
    """Stands in for what `with httpx.stream(...) as response:` yields —
    a context manager whose body is only read via `iter_bytes()`, matching
    the real download path (never `.content`, since the whole point is to
    never load an unbounded external file into memory in one shot)."""

    def __init__(self, status_code: int, chunks: list[bytes] | None = None, headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []

    def iter_bytes(self):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _discover_page_html(campaigns: dict) -> str:
    """A minimal stand-in for the real page's response text: this provider
    only ever looks for the `{"bannerCampaigns":...}` JSON object inside a
    much larger blob of surrounding markup, so wrapping it in unrelated
    text here exercises that it's actually located rather than assumed to
    be the entire response."""
    return f"<html><script>self.__next_f.push([1,{json.dumps(json.dumps(campaigns))}])</script></html>"


def test_manual_provider_returns_no_videos():
    assert ManualContentSourceProvider().list_available_videos() == []


def test_manual_provider_raises_on_download():
    with pytest.raises(NotImplementedError):
        ManualContentSourceProvider().download_video(_video(), "/tmp/whatever.mp4")


def test_factory_returns_manual_provider_by_default():
    settings = Settings(content_source_provider="manual")
    assert isinstance(get_content_source_provider(settings), ManualContentSourceProvider)


def test_factory_returns_content_rewards_provider_when_configured():
    settings = Settings(content_source_provider="content_rewards")
    assert isinstance(get_content_source_provider(settings), ContentRewardsProvider)


def test_factory_warns_loudly_when_content_rewards_provider_is_selected(monkeypatch):
    """Real safety requirement: CONTENT_SOURCE_PROVIDER defaults to
    "manual" (no video source at all) and is never set in render.yaml -
    selecting ContentRewardsProvider requires an operator to explicitly set
    this env var. If that ever happens by mistake, this warning must fire
    on every single resolution so it's impossible to miss in production
    logs that real external requests (contentrewards.com, Google Drive)
    are now in play.

    Asserts against the logger call directly (monkeypatched) rather than
    captured output: this codebase's structlog is configured with
    PrintLoggerFactory (see logging_config.py), which writes straight to
    stdout rather than through stdlib logging - pytest's `caplog` fixture,
    which hooks stdlib logging, never sees it."""
    from content_factory.content_sources import factory as factory_module

    warnings = []
    monkeypatch.setattr(factory_module.logger, "warning", lambda event, **kw: warnings.append(event))

    settings = Settings(content_source_provider="content_rewards")
    get_content_source_provider(settings)

    assert "content_source_provider_is_content_rewards" in warnings


def test_factory_does_not_warn_for_the_default_manual_provider(monkeypatch):
    from content_factory.content_sources import factory as factory_module

    warnings = []
    monkeypatch.setattr(factory_module.logger, "warning", lambda event, **kw: warnings.append(event))

    settings = Settings(content_source_provider="manual")
    get_content_source_provider(settings)

    assert warnings == []


def test_factory_raises_on_unknown_provider():
    settings = Settings(content_source_provider="something_else")
    with pytest.raises(ValueError):
        get_content_source_provider(settings)


def test_content_rewards_provider_lists_only_campaigns_with_a_drive_link(monkeypatch):
    """Real architecture discovered this session: many campaigns hide their
    footage behind a separate per-campaign Whop membership instead of a
    public link - those must be silently skipped, not guessed at."""
    campaigns = {
        "bannerCampaigns": [
            {
                "id": "camp-1",
                "title": "Has a Drive link",
                "brand": "Brand A",
                "description": "Raw footage: https://drive.google.com/drive/folders/abc123XYZ",
            },
        ],
        "featuredCampaigns": [
            {
                "id": "camp-2",
                "title": "No public link (locked Whop app)",
                "brand": "Brand B",
                "description": "Join our exclusive content vault to get footage.",
            },
        ],
        "featuredMixCampaigns": [],
        "success": True,
    }
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse(200, text=_discover_page_html(campaigns))
    )

    videos = ContentRewardsProvider().list_available_videos()

    assert len(videos) == 1
    assert videos[0].external_id == "camp-1"
    assert videos[0].campaign_name == "Brand A"
    assert videos[0].download_url == "https://drive.google.com/drive/folders/abc123XYZ"


def test_content_rewards_provider_raises_retryable_on_timeout(monkeypatch):
    def _raise_timeout(*a, **k):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "get", _raise_timeout)
    with pytest.raises(RetryableProviderError):
        ContentRewardsProvider().list_available_videos()


def test_content_rewards_provider_raises_retryable_on_5xx(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(503))
    with pytest.raises(RetryableProviderError):
        ContentRewardsProvider().list_available_videos()


def test_content_rewards_provider_raises_rejected_on_4xx(monkeypatch):
    """Covers the real, documented risk that Cloudflare blocks a
    non-browser request with a 403 - surfaced the same way as any other
    provider's rejected request, not a crash."""
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(403))
    with pytest.raises(ProviderRequestRejected):
        ContentRewardsProvider().list_available_videos()


def test_content_rewards_provider_raises_rejected_when_markup_has_changed(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, text="<html>nothing here</html>"))
    with pytest.raises(ProviderRequestRejected):
        ContentRewardsProvider().list_available_videos()


def test_content_rewards_provider_download_requires_google_drive_api_key():
    provider = ContentRewardsProvider(google_drive_api_key="")
    video = RemoteCampaignVideo(
        external_id="camp-1", title="t", campaign_name="c", duration_s=None,
        download_url="https://drive.google.com/drive/folders/abc123XYZ", source_page_url="",
    )
    with pytest.raises(RuntimeError):
        provider.download_video(video, "/tmp/whatever.mp4")


def _video_with_drive_folder() -> RemoteCampaignVideo:
    return RemoteCampaignVideo(
        external_id="camp-1", title="t", campaign_name="c", duration_s=None,
        download_url="https://drive.google.com/drive/folders/abc123XYZ", source_page_url="",
    )


def _mock_folder_listing(monkeypatch, files: list[dict]) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, json_body={"files": files}))


def test_content_rewards_provider_downloads_the_first_video_file_in_the_folder(monkeypatch, tmp_path):
    _mock_folder_listing(monkeypatch, [{"id": "file-1", "name": "raw.mp4", "mimeType": "video/mp4"}])
    monkeypatch.setattr(
        httpx, "stream",
        lambda method, url, **k: _FakeStreamResponse(200, chunks=[b"fake ", b"mp4 bytes"]),
    )

    provider = ContentRewardsProvider(google_drive_api_key="test-key")
    dest = tmp_path / "out.mp4"

    provider.download_video(_video_with_drive_folder(), str(dest))

    assert dest.read_bytes() == b"fake mp4 bytes"


def test_content_rewards_provider_download_raises_when_folder_has_no_videos(monkeypatch):
    _mock_folder_listing(monkeypatch, [])

    provider = ContentRewardsProvider(google_drive_api_key="test-key")
    with pytest.raises(ProviderRequestRejected):
        provider.download_video(_video_with_drive_folder(), "/tmp/whatever.mp4")


def test_content_rewards_provider_download_rejects_when_content_length_exceeds_limit(monkeypatch, tmp_path):
    """A Google Drive folder is external, uncontrolled content - the size
    cap must be enforced from the Content-Length header up front, without
    ever writing a byte to disk, when the server sends one."""
    _mock_folder_listing(monkeypatch, [{"id": "file-1", "name": "raw.mp4", "mimeType": "video/mp4"}])
    monkeypatch.setattr(
        httpx, "stream",
        lambda method, url, **k: _FakeStreamResponse(
            200, chunks=[b"x" * 100], headers={"content-length": "999999999999"}
        ),
    )

    provider = ContentRewardsProvider(google_drive_api_key="test-key", max_video_bytes=1000)
    dest = tmp_path / "out.mp4"

    with pytest.raises(ProviderRequestRejected):
        provider.download_video(_video_with_drive_folder(), str(dest))

    assert not dest.exists()


def test_content_rewards_provider_download_stops_and_cleans_up_when_streamed_bytes_exceed_limit(monkeypatch, tmp_path):
    """No Content-Length header at all this time - the limit must still be
    enforced by counting actual bytes as they stream in, and any partial
    file already written must be removed, not left behind half-downloaded."""
    _mock_folder_listing(monkeypatch, [{"id": "file-1", "name": "raw.mp4", "mimeType": "video/mp4"}])
    monkeypatch.setattr(
        httpx, "stream",
        lambda method, url, **k: _FakeStreamResponse(200, chunks=[b"x" * 600, b"y" * 600]),
    )

    provider = ContentRewardsProvider(google_drive_api_key="test-key", max_video_bytes=1000)
    dest = tmp_path / "out.mp4"

    with pytest.raises(ProviderRequestRejected):
        provider.download_video(_video_with_drive_folder(), str(dest))

    assert not dest.exists()
