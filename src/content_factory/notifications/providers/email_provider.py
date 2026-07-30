"""Real email provider via stdlib `smtplib`/`email` — no optional extra
needed, unlike Slack/httpx, since SMTP support ships with Python itself.
Selected only when NOTIFICATION_PROVIDER=email and SMTP settings are
configured (see notifications/factory.py).
"""

import smtplib
from email.message import EmailMessage

from content_factory.notifications.base import NotificationProvider, NotificationRequest, NotificationResult


class EmailNotificationProvider(NotificationProvider):
    def __init__(self, *, smtp_host: str, smtp_port: int, from_addr: str, to_addr: str) -> None:
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._from_addr = from_addr
        self._to_addr = to_addr

    def send(self, request: NotificationRequest) -> NotificationResult:
        message = EmailMessage()
        message["Subject"] = f"[{request.severity.value.upper()}] {request.subject}"
        message["From"] = self._from_addr
        message["To"] = self._to_addr
        message.set_content(request.body)

        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as smtp:
                smtp.send_message(message)
        except OSError:
            return NotificationResult(channel="email", delivered=False)
        return NotificationResult(channel="email", delivered=True)
