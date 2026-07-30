"""Production Hardening Sprint H6: observability.py's configure_metrics/
configure_error_tracking are no-ops unless explicitly enabled/configured —
same "safe default, real implementation behind a lazy import" contract as
every provider factory in this codebase."""

from fastapi import FastAPI

from content_factory.config import Settings
from content_factory.observability import configure_error_tracking, configure_metrics


def test_configure_metrics_mounts_endpoint_when_enabled():
    app = FastAPI()
    configure_metrics(app, Settings(metrics_enabled=True))
    assert any(getattr(route, "path", None) == "/metrics" for route in app.routes)


def test_configure_metrics_does_nothing_when_disabled():
    app = FastAPI()
    routes_before = list(app.routes)
    configure_metrics(app, Settings(metrics_enabled=False))
    assert list(app.routes) == routes_before


def test_configure_error_tracking_does_nothing_without_a_dsn(monkeypatch):
    """No DSN is the default and every existing test's exact environment —
    sentry_sdk.init must never be called in that state."""
    import sentry_sdk

    called = False

    def _fake_init(**kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(sentry_sdk, "init", _fake_init)
    configure_error_tracking(Settings(sentry_dsn=""))
    assert called is False


def test_configure_error_tracking_initializes_sentry_when_dsn_configured(monkeypatch):
    import sentry_sdk

    captured = {}

    def _fake_init(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(sentry_sdk, "init", _fake_init)
    configure_error_tracking(Settings(sentry_dsn="https://example@sentry.example.com/1", environment="production"))
    assert captured["dsn"] == "https://example@sentry.example.com/1"
    assert captured["environment"] == "production"
