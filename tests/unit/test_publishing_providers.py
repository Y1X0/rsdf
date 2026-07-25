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
from content_factory.publishing.retry import RetryableProviderError


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict):
        self.status_code = status_code
        self._json_body = json_body

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
    with pytest.raises(httpx.HTTPStatusError):
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
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(200, {"id": "ig-123"}))
    result = InstagramPublishingProvider(access_token="token").publish(_request())
    assert result.provider == "instagram"
    assert result.published is True
    assert result.external_post_id == "ig-123"


def test_instagram_provider_raises_retryable_on_5xx(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(502, {}))
    with pytest.raises(RetryableProviderError):
        InstagramPublishingProvider(access_token="token").publish(_request())
