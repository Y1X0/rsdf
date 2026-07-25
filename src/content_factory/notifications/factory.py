from content_factory.config import Settings
from content_factory.logging_config import get_logger
from content_factory.notifications.base import NotificationProvider
from content_factory.notifications.providers.log_provider import LogNotificationProvider

logger = get_logger(__name__)


def get_notification_provider(settings: Settings) -> NotificationProvider:
    provider = settings.resolved_notification_provider()

    if provider == "slack":
        from content_factory.notifications.providers.slack_provider import SlackNotificationProvider

        return SlackNotificationProvider(webhook_url=settings.slack_webhook_url)

    if provider == "email":
        from content_factory.notifications.providers.email_provider import EmailNotificationProvider

        return EmailNotificationProvider(
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            from_addr=settings.smtp_from_addr,
            to_addr=settings.smtp_to_addr,
        )

    if provider == "log":
        if settings.notification_provider != "log":
            logger.warning(
                "notification_provider_fallback", reason="not_configured", configured=settings.notification_provider
            )
        return LogNotificationProvider()

    raise ValueError(f"Unknown notification provider: {provider!r}")
