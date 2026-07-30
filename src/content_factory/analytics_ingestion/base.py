"""PlatformAnalyticsProvider — the interface every platform metrics-fetch
integration sits behind (ARCHITECTURE.md §2.5), same shape as
publishing/base.py: business logic (api/routers/publications.py's sync
endpoint) depends only on this ABC, never on a concrete TikTok/YouTube/
Instagram analytics client directly. The fetched result is fed through the
*existing*, unchanged `services/analytics_service.record_metrics` — this
package only ever produces the same shape that endpoint already accepts
manually.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AnalyticsFetchResult:
    views: int
    avg_watch_time_s: float | None = None
    completion_rate: float | None = None
    rewatch_rate: float | None = None
    shares: int = 0
    comments: int = 0
    likes: int = 0
    saves: int = 0


class MetricsNotAutomated(Exception):
    """Raised by ManualAnalyticsProvider: no automated analytics
    integration is configured for this platform/account. Unlike
    publishing (where the manual default still does something useful —
    readies content for a human to post), there is no meaningful
    placeholder metrics fetch — the operator should use the existing
    `POST /videos/{video_id}/metrics` endpoint directly instead."""


class PlatformAnalyticsProvider(ABC):
    @abstractmethod
    def fetch_metrics(self, *, external_post_id: str) -> AnalyticsFetchResult:
        raise NotImplementedError
