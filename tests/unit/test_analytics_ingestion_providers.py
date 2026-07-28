"""Phase 2 M5: real platform analytics providers, tested against mocked
HTTP responses only — same zero-live-network philosophy as
test_publishing_providers.py."""

import httpx
import pytest

from content_factory.analytics_ingestion.base import MetricsNotAutomated
from content_factory.analytics_ingestion.providers.instagram_provider import InstagramAnalyticsProvider
from content_factory.analytics_ingestion.providers.manual_provider import ManualAnalyticsProvider
from content_factory.analytics_ingestion.providers.tiktok_provider import TikTokAnalyticsProvider
from content_factory.analytics_ingestion.providers.youtube_provider import YouTubeAnalyticsProvider
from content_factory.retry import RetryableProviderError


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict):
        self.status_code = status_code
        self._json_body = json_body

    def json(self) -> dict:
        return self._json_body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def test_manual_provider_raises_metrics_not_automated():
    with pytest.raises(MetricsNotAutomated):
        ManualAnalyticsProvider().fetch_metrics(external_post_id="123")


def test_tiktok_provider_parses_metrics(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(
            200, {"data": {"videos": [{"view_count": 100, "like_count": 20, "comment_count": 5, "share_count": 3}]}}
        ),
    )
    result = TikTokAnalyticsProvider(access_token="token").fetch_metrics(external_post_id="abc")
    assert result.views == 100
    assert result.likes == 20
    assert result.comments == 5
    assert result.shares == 3


def test_tiktok_provider_raises_retryable_on_5xx(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(503, {}))
    with pytest.raises(RetryableProviderError):
        TikTokAnalyticsProvider(access_token="token").fetch_metrics(external_post_id="abc")


def test_youtube_provider_parses_metrics(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: _FakeResponse(
            200, {"items": [{"statistics": {"viewCount": "500", "likeCount": "50", "commentCount": "10"}}]}
        ),
    )
    result = YouTubeAnalyticsProvider(access_token="token").fetch_metrics(external_post_id="xyz")
    assert result.views == 500
    assert result.likes == 50
    assert result.comments == 10


def test_youtube_provider_raises_retryable_on_5xx(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(500, {}))
    with pytest.raises(RetryableProviderError):
        YouTubeAnalyticsProvider(access_token="token").fetch_metrics(external_post_id="xyz")


def test_instagram_provider_parses_metrics(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: _FakeResponse(
            200,
            {
                "data": [
                    {"name": "plays", "values": [{"value": 200}]},
                    {"name": "likes", "values": [{"value": 30}]},
                    {"name": "comments", "values": [{"value": 4}]},
                ]
            },
        ),
    )
    result = InstagramAnalyticsProvider(access_token="token").fetch_metrics(external_post_id="media-1")
    assert result.views == 200
    assert result.likes == 30
    assert result.comments == 4


def test_instagram_provider_raises_retryable_on_5xx(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(502, {}))
    with pytest.raises(RetryableProviderError):
        InstagramAnalyticsProvider(access_token="token").fetch_metrics(external_post_id="media-1")
