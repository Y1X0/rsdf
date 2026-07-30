"""Real Instagram Graph API analytics provider (media insights). `httpx`
is only imported here, lazily (install with `pip install '.[publishing]'`).
Never exercised against the live API in this environment (ARCHITECTURE.md
§13's Meta App Review caveat) — unit-tested against mocked HTTP responses
only.
"""

from content_factory.analytics_ingestion.base import AnalyticsFetchResult, PlatformAnalyticsProvider
from content_factory.retry import RetryableProviderError

_INSIGHTS_URL_TEMPLATE = "https://graph.facebook.com/v19.0/{media_id}/insights"


class InstagramAnalyticsProvider(PlatformAnalyticsProvider):
    def __init__(self, access_token: str) -> None:
        self._access_token = access_token

    def fetch_metrics(self, *, external_post_id: str) -> AnalyticsFetchResult:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "InstagramAnalyticsProvider requires the 'publishing' extra: pip install '.[publishing]'"
            ) from exc

        try:
            response = httpx.get(
                _INSIGHTS_URL_TEMPLATE.format(media_id=external_post_id),
                params={"access_token": self._access_token, "metric": "plays,likes,comments,shares,saved"},
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("Instagram analytics request timed out") from exc

        if response.status_code >= 500:
            raise RetryableProviderError(f"Instagram analytics returned {response.status_code}")
        response.raise_for_status()

        values = {
            entry.get("name"): (entry.get("values") or [{}])[0].get("value", 0)
            for entry in response.json().get("data", [])
        }
        return AnalyticsFetchResult(
            views=int(values.get("plays", 0)),
            likes=int(values.get("likes", 0)),
            comments=int(values.get("comments", 0)),
            shares=int(values.get("shares", 0)),
            saves=int(values.get("saved", 0)),
        )
