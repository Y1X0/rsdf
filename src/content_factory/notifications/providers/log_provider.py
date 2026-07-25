"""Default notification backend: no external dependency, always available
— the `NullRenderer`/`SilentTTSProvider`/`ManualPublishingProvider` pattern
applied to alerting. Structured-logs the notification via structlog; the
caller (services/budget_governor.py) is still responsible for persisting
the audit row (NotificationLog) — providers here never touch the database
directly, matching every other provider in this codebase.
"""

from content_factory.logging_config import get_logger
from content_factory.notifications.base import NotificationProvider, NotificationRequest, NotificationResult

logger = get_logger(__name__)


class LogNotificationProvider(NotificationProvider):
    def send(self, request: NotificationRequest) -> NotificationResult:
        logger.warning(
            "notification",
            severity=request.severity.value,
            subject=request.subject,
            body=request.body,
            related_entity_type=request.related_entity_type,
            related_entity_id=request.related_entity_id,
        )
        return NotificationResult(channel="log", delivered=True)
