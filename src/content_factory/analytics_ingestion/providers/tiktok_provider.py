"""Real TikTok analytics provider (Video List / Query APIs). `httpx` is
only imported here, lazily (install with `pip install '.[publishing]'` —
the same extra as the publishing providers, since it's the same
credentials/access-token surface). Never exercised against the live API in
this environment (ARCHITECTURE.md §0/§13's app-review caveat) — unit-tested
against mocked HTTP responses only.
"""

from content_factory.analytics_ingestion.base import AnalyticsFetchResult, PlatformAnalyticsProvider
from content_factory.publishing.retry import RetryableProviderError

_QUERY_URL = "https://open.tiktokapis.com/v2/video/query/"


class TikTokAnalyticsProvider(PlatformAnalyticsProvider):
    def __init__(self, access_token: str) -> None:
        self._access_token = access_token

    def fetch_metrics(self, *, external_post_id: str) -> AnalyticsFetchResult:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "TikTokAnalyticsProvider requires the 'publishing' extra: pip install '.[publishing]'"
            ) from exc

        try:
            response = httpx.post(
                _QUERY_URL,
                headers={"Authorization": f"Bearer {self._access_token}"},
                json={"filters": {"video_ids": [external_post_id]}},
                params={"fields": "view_count,like_count,comment_count,share_count"},
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("TikTok analytics request timed out") from exc

        if response.status_code >= 500:
            raise RetryableProviderError(f"TikTok analytics returned {response.status_code}")
        response.raise_for_status()

        video = (response.json().get("data", {}).get("videos") or [{}])[0]
        return AnalyticsFetchResult(
            views=int(video.get("view_count", 0)),
            likes=int(video.get("like_count", 0)),
            comments=int(video.get("comment_count", 0)),
            shares=int(video.get("share_count", 0)),
        )
