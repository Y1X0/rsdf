"""Real Slack provider. `httpx` is only imported here, lazily — the core
install has no hard dependency on it. Selected only when
NOTIFICATION_PROVIDER=slack and SLACK_WEBHOOK_URL is configured (see
notifications/factory.py); falls back to LogNotificationProvider otherwise.
"""

from content_factory.notifications.base import NotificationProvider, NotificationRequest, NotificationResult


class SlackNotificationProvider(NotificationProvider):
    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url

    def send(self, request: NotificationRequest) -> NotificationResult:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "SlackNotificationProvider requires the 'notifications' extra: "
                "pip install '.[notifications]'"
            ) from exc

        response = httpx.post(
            self._webhook_url,
            json={"text": f"*[{request.severity.value.upper()}] {request.subject}*\n{request.body}"},
            timeout=10.0,
        )
        return NotificationResult(channel="slack", delivered=response.status_code < 300)
