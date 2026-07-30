from content_factory.analytics_ingestion.base import (
    AnalyticsFetchResult,
    MetricsNotAutomated,
    PlatformAnalyticsProvider,
)


class ManualAnalyticsProvider(PlatformAnalyticsProvider):
    def fetch_metrics(self, *, external_post_id: str) -> AnalyticsFetchResult:
        raise MetricsNotAutomated(
            "No automated analytics integration is configured for this platform. "
            "Use POST /videos/{video_id}/metrics to enter metrics manually."
        )
