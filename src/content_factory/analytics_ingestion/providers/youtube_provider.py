"""Real YouTube Data API v3 analytics provider (videos.list statistics).
`httpx` is only imported here, lazily (install with
`pip install '.[publishing]'`). Never exercised against the live API in
this environment (ARCHITECTURE.md §13's quota-limit caveat) — unit-tested
against mocked HTTP responses only.
"""

from content_factory.analytics_ingestion.base import AnalyticsFetchResult, PlatformAnalyticsProvider
from content_factory.retry import RetryableProviderError

_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


class YouTubeAnalyticsProvider(PlatformAnalyticsProvider):
    def __init__(self, access_token: str) -> None:
        self._access_token = access_token

    def fetch_metrics(self, *, external_post_id: str) -> AnalyticsFetchResult:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "YouTubeAnalyticsProvider requires the 'publishing' extra: pip install '.[publishing]'"
            ) from exc

        try:
            response = httpx.get(
                _VIDEOS_URL,
                headers={"Authorization": f"Bearer {self._access_token}"},
                params={"part": "statistics", "id": external_post_id},
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("YouTube analytics request timed out") from exc

        if response.status_code >= 500:
            raise RetryableProviderError(f"YouTube analytics returned {response.status_code}")
        response.raise_for_status()

        items = response.json().get("items") or [{}]
        stats = items[0].get("statistics", {})
        return AnalyticsFetchResult(
            views=int(stats.get("viewCount", 0)),
            likes=int(stats.get("likeCount", 0)),
            comments=int(stats.get("commentCount", 0)),
        )
