"""Phase 2 M4: real platform providers, tested against mocked HTTP
responses only — never a live network call, matching this codebase's
zero-secrets-required test philosophy (and closing audit finding F13 for
every *new* provider this phase adds)."""

import httpx
import pytest

from content_factory.publishing.base import PublishRequest
from content_factory.publishing.providers.instagram_provider import InstagramPublishingProvider
from content_factory.publishing.providers.tiktok_provider import TikTokPublishingProvider
from content_factory.publishing.providers.youtube_provider import YouTubePublishingProvider
from content_factory.retry import ProviderRequestRejected, RetryableProviderError


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict, text: str = ""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text or str(json_body)

    def json(self) -> dict:
        return self._json_body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def _request() -> PublishRequest:
    return PublishRequest(
        video_id=1,
        asset_url="https://example.com/video.mp4",
        title="Test title",
        description="Test description",
        hashtags=["fyp", "test"],
        contains_ai_voice=True,
        contains_ai_visual=True,
    )


def test_tiktok_provider_publishes_successfully(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(200, {"data": {"publish_id": "tiktok-123"}})
    )
    result = TikTokPublishingProvider(access_token="token").publish(_request())
    assert result.provider == "tiktok"
    assert result.published is True
    assert result.external_post_id == "tiktok-123"


def test_tiktok_provider_raises_retryable_on_5xx(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(503, {}))
    with pytest.raises(RetryableProviderError):
        TikTokPublishingProvider(access_token="token").publish(_request())


def test_tiktok_provider_raises_retryable_on_timeout(monkeypatch):
    def _raise_timeout(*a, **k):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", _raise_timeout)
    with pytest.raises(RetryableProviderError):
        TikTokPublishingProvider(access_token="token").publish(_request())


def test_tiktok_provider_does_not_retry_on_4xx(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(401, {}))
    with pytest.raises(ProviderRequestRejected):
        TikTokPublishingProvider(access_token="token").publish(_request())


def test_youtube_provider_publishes_successfully(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(200, {"id": "yt-123"}))
    result = YouTubePublishingProvider(access_token="token").publish(_request())
    assert result.provider == "youtube"
    assert result.published is True
    assert result.external_post_id == "yt-123"


def test_youtube_provider_raises_retryable_on_5xx(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(500, {}))
    with pytest.raises(RetryableProviderError):
        YouTubePublishingProvider(access_token="token").publish(_request())


def test_instagram_provider_publishes_successfully(monkeypatch):
    """Real Instagram publishing is a real three-call sequence: create a
    media container, poll until Meta finishes processing it, then publish
    that container - a single call to /media_publish (the old, wrong
    behavior) was never valid against the real Graph API."""
    posts = []
    gets = []

    def _fake_post(url, **kwargs):
        posts.append((url, kwargs))
        if url.endswith("/media"):
            return _FakeResponse(200, {"id": "creation-123"})
        if url.endswith("/media_publish"):
            assert kwargs["json"]["creation_id"] == "creation-123"
            return _FakeResponse(200, {"id": "ig-123"})
        raise AssertionError(f"unexpected POST url: {url}")

    def _fake_get(url, **kwargs):
        gets.append((url, kwargs))
        return _FakeResponse(200, {"status_code": "FINISHED"})

    monkeypatch.setattr(httpx, "post", _fake_post)
    monkeypatch.setattr(httpx, "get", _fake_get)

    result = InstagramPublishingProvider(access_token="token", account_id="17841440632369231").publish(_request())

    assert result.provider == "instagram"
    assert result.published is True
    assert result.external_post_id == "ig-123"
    assert len(posts) == 2
    assert "17841440632369231/media" in posts[0][0]
    assert "17841440632369231/media_publish" in posts[1][0]
    assert len(gets) == 1
    assert "creation-123" in gets[0][0]


def test_instagram_provider_polls_until_container_is_finished(monkeypatch):
    statuses = iter(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])

    monkeypatch.setattr(httpx, "post", lambda url, **k: _FakeResponse(200, {"id": "ig-123"}))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, {"status_code": next(statuses)}))
    monkeypatch.setattr("content_factory.publishing.providers.instagram_provider.time.sleep", lambda *_: None)

    result = InstagramPublishingProvider(access_token="token", account_id="123").publish(_request())
    assert result.published is True


def test_instagram_provider_raises_on_container_processing_error(monkeypatch):
    from content_factory.publishing.providers.instagram_provider import InstagramContainerProcessingFailed

    monkeypatch.setattr(httpx, "post", lambda url, **k: _FakeResponse(200, {"id": "creation-123"}))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, {"status_code": "ERROR"}))

    with pytest.raises(InstagramContainerProcessingFailed):
        InstagramPublishingProvider(access_token="token", account_id="123").publish(_request())


def test_instagram_provider_raises_retryable_when_container_never_finishes(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **k: _FakeResponse(200, {"id": "creation-123"}))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, {"status_code": "IN_PROGRESS"}))
    monkeypatch.setattr("content_factory.publishing.providers.instagram_provider.time.sleep", lambda *_: None)

    with pytest.raises(RetryableProviderError):
        InstagramPublishingProvider(access_token="token", account_id="123").publish(_request())


def test_instagram_provider_raises_retryable_on_5xx_during_container_creation(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **k: _FakeResponse(502, {}))
    with pytest.raises(RetryableProviderError):
        InstagramPublishingProvider(access_token="token", account_id="123").publish(_request())


def test_instagram_provider_defaults_account_id_to_me():
    provider = InstagramPublishingProvider(access_token="token")
    assert provider._account_id == "me"


def test_instagram_provider_surfaces_the_real_graph_api_error_body_on_4xx(monkeypatch):
    """Real production bug this closes: the first real Instagram publish
    attempt failed with nothing more useful logged anywhere than "Client
    error '400 Bad Request' for url '...'" - response.raise_for_status()
    never includes the response body, which for the real Graph API is a
    real, actionable {"error": {"message": ..., "code": ..., ...}} object.
    This proves that message now reaches the raised exception."""
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            400,
            {"error": {"message": "Invalid parameter", "type": "OAuthException", "code": 100}},
        ),
    )
    with pytest.raises(ProviderRequestRejected, match="Invalid parameter"):
        InstagramPublishingProvider(access_token="token", account_id="123").publish(_request())


def test_youtube_provider_does_not_retry_on_4xx_and_surfaces_body(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(403, {"error": {"message": "quotaExceeded"}})
    )
    with pytest.raises(ProviderRequestRejected, match="quotaExceeded"):
        YouTubePublishingProvider(access_token="token").publish(_request())
