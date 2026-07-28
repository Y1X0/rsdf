"""SlackNotificationProvider - tested against mocked HTTP responses only,
never a live network call, matching this codebase's zero-secrets-required
test philosophy."""

import httpx

from content_factory.db.models.enums import NotificationSeverity
from content_factory.notifications.base import NotificationRequest
from content_factory.notifications.providers.slack_provider import SlackNotificationProvider


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def _request() -> NotificationRequest:
    return NotificationRequest(severity=NotificationSeverity.WARNING, subject="Budget 80% crossed", body="details")


def test_send_reports_delivered_on_a_2xx_response(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(200))
    provider = SlackNotificationProvider(webhook_url="https://hooks.slack.example/x")

    result = provider.send(_request())

    assert result.channel == "slack"
    assert result.delivered is True


def test_send_reports_not_delivered_on_a_non_2xx_response(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(500))
    provider = SlackNotificationProvider(webhook_url="https://hooks.slack.example/x")

    result = provider.send(_request())

    assert result.delivered is False


def test_send_never_raises_on_a_connection_failure(monkeypatch):
    """Regression test: this provider previously had zero exception
    handling around the network call at all. A notification is a
    best-effort side-channel - budget_governor._maybe_alert() calls
    provider.send() with no try/except of its own, from the top of real
    cost-incurring endpoints (render, script generation) via
    check_budget() - so a Slack outage must not crash the actual request,
    matching EmailNotificationProvider's own OSError handling."""

    def _raise_connect_error(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _raise_connect_error)
    provider = SlackNotificationProvider(webhook_url="https://hooks.slack.example/x")

    result = provider.send(_request())

    assert result.channel == "slack"
    assert result.delivered is False


def test_send_never_raises_on_a_timeout(monkeypatch):
    def _raise_timeout(*a, **k):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", _raise_timeout)
    provider = SlackNotificationProvider(webhook_url="https://hooks.slack.example/x")

    result = provider.send(_request())

    assert result.delivered is False
