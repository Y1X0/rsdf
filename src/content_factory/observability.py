"""Observability wiring (Production Hardening Sprint H6) — closes the
production readiness review's M1 (no metrics), M2 (no request
correlation), and M3 (no error tracking) findings.

Follows the same "safe default, real implementation behind a lazy
import" pattern as every provider in this codebase: metrics are mounted
only if `prometheus-fastapi-instrumentator` is installed (the
`observability`/`production` extra), and Sentry is only initialized if
both `sentry-sdk` is installed *and* `SENTRY_DSN` is configured — the
base install and every existing test run with neither, unaffected.
"""

from fastapi import FastAPI

from content_factory.config import Settings
from content_factory.logging_config import get_logger

logger = get_logger(__name__)


def configure_metrics(app: FastAPI, settings: Settings) -> None:
    """Mounts a Prometheus-format `/metrics` endpoint and instruments every
    route with request count/latency histograms. A no-op if
    `METRICS_ENABLED=false` or the `observability` extra isn't installed —
    logged once at startup either way, so a misconfigured deployment finds
    out from its own boot log rather than by 404-ing on `/metrics` later."""
    if not settings.metrics_enabled:
        logger.info("metrics_disabled", reason="METRICS_ENABLED is false")
        return

    try:
        from prometheus_fastapi_instrumentator import Instrumentator
    except ImportError:
        logger.warning(
            "metrics_unavailable",
            reason="prometheus-fastapi-instrumentator not installed (pip install '.[observability]')",
        )
        return

    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    logger.info("metrics_enabled", endpoint="/metrics")


def configure_error_tracking(settings: Settings) -> None:
    """Initializes Sentry only when a DSN is actually configured — sentry_sdk
    is never even imported otherwise, matching the zero-secrets-required
    default every other provider in this codebase already guarantees."""
    if not settings.sentry_dsn:
        logger.info("error_tracking_disabled", reason="SENTRY_DSN is unset")
        return

    try:
        import sentry_sdk
    except ImportError:
        logger.warning(
            "error_tracking_unavailable",
            reason="sentry-sdk not installed (pip install '.[observability]')",
        )
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        release="content-factory@2.0.0",
    )
    logger.info("error_tracking_enabled", environment=settings.environment)
