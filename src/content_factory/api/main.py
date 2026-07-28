import uuid
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text

from content_factory.api.routers import (
    accounts,
    analytics,
    auth,
    budget,
    campaigns,
    clips,
    content,
    dashboard,
    experimentation,
    niches,
    publications,
    review,
)
from content_factory.config import get_settings
from content_factory.db.base import engine
from content_factory.logging_config import configure_logging, get_logger
from content_factory.observability import configure_error_tracking, configure_metrics
from content_factory.services.budget_governor import BudgetExceeded
from content_factory.services.idempotency import IdempotencyConflict, IdempotencyInProgress

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    # Production Hardening Sprint H1: boot-time fail-closed check — see
    # Settings.validate_production_safety's own docstring for why this
    # exists. A no-op unless ENVIRONMENT=production is explicitly set.
    settings.validate_production_safety()
    # Production Hardening Sprint H6: both are no-ops unless configured/
    # installed — see observability.py's own docstring.
    configure_error_tracking(settings)

    app = FastAPI(
        title="AI Content Factory",
        description="Whop Content Rewards content pipeline. See docs/ARCHITECTURE.md and docs/PHASE1.md.",
        version="2.0.0",
    )

    @app.middleware("http")
    async def _request_id_correlation(request: Request, call_next):
        """Production Hardening Sprint H6: binds a request ID to every
        structlog event emitted while handling this request (via
        structlog's contextvars, already wired into logging_config.py's
        processor chain), so every log line from one HTTP request —
        across routers, services, and agents — can be grepped together.
        Accepts an inbound X-Request-ID (so a caller/gateway can supply
        its own trace ID) or generates one; always echoes it back in the
        response header either way."""
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    app.include_router(auth.router)
    app.include_router(campaigns.router)
    app.include_router(niches.router)
    app.include_router(content.router)
    app.include_router(review.router)
    app.include_router(analytics.router)
    app.include_router(dashboard.router)
    app.include_router(budget.router)
    app.include_router(accounts.router)
    app.include_router(publications.router)
    app.include_router(experimentation.router)
    app.include_router(clips.router)

    @app.exception_handler(IdempotencyConflict)
    def _handle_idempotency_conflict(request: Request, exc: IdempotencyConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(IdempotencyInProgress)
    def _handle_idempotency_in_progress(request: Request, exc: IdempotencyInProgress) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(BudgetExceeded)
    def _handle_budget_exceeded(request: Request, exc: BudgetExceeded) -> JSONResponse:
        # 402 Payment Required — fail closed per ARCHITECTURE.md §10c/§20:
        # a monthly ceiling is a human-set decision, so the system never
        # auto-raises it, it just stops spending until someone does.
        return JSONResponse(status_code=402, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # v1.1: previously there was no catch-all handler, so any unhandled
        # service exception propagated past Starlette's TestClient as a raw
        # Python exception instead of an HTTP response — which is also
        # exactly what happens with a real ASGI server in production
        # (uvicorn returns a bare 500 with no logged context of its own).
        # This logs the failure with full context and returns a generic,
        # non-leaking error body.
        logger.error("unhandled_exception", path=str(request.url.path), method=request.method, exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @app.get("/health", tags=["health"])
    def health() -> dict:
        # Uses its own short-lived connection, independent of the
        # request-scoped session (api/deps.get_db) — a liveness check
        # shouldn't share transaction state with business requests, and
        # this way a failed check can't leave a session in an aborted-
        # transaction state for anything else to trip over.
        checks: dict[str, str] = {}

        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:
            logger.error("health_check_db_failed", exc_info=True)
            checks["database"] = "unreachable"

        # Production Hardening Sprint H6: only checked when Redis is
        # actually part of this deployment's configuration (the rate
        # limiter backend) — an unconfigured Redis isn't a health problem,
        # it's just not in use (see auth/rate_limiter_factory.py).
        current_settings = get_settings()
        if current_settings.rate_limit_backend == "redis" and current_settings.redis_url:
            try:
                import redis

                redis.Redis.from_url(current_settings.redis_url).ping()
                checks["redis"] = "ok"
            except Exception:
                logger.error("health_check_redis_failed", exc_info=True)
                checks["redis"] = "unreachable"

        # Purely informational - never affects the 200/503 status above.
        # Render's own healthCheckPath points at this endpoint, so a
        # provider resolving to a placeholder (e.g. TRANSCRIPTION_PROVIDER
        # never set, silently defaulting to "null") must never make this
        # endpoint report unhealthy - that's a real deploy-blocking
        # incident, not a liveness failure. This exists precisely because
        # that exact misconfiguration once shipped silently: nothing
        # surfaced it short of actually running the whole pipeline and
        # noticing the output was a JSON manifest instead of a real video.
        pipeline = {
            "transcription_provider": current_settings.resolved_transcription_provider(),
            "clip_renderer_backend": current_settings.clip_renderer_backend,
            "llm_provider": current_settings.resolved_llm_provider(),
            "media_backup_enabled": current_settings.media_backup_enabled,
            "media_backup_publicly_hostable": bool(
                current_settings.media_backup_enabled and current_settings.media_backup_public_base_url
            ),
            "publishing_enabled": current_settings.publishing_enabled,
        }

        if any(status == "unreachable" for status in checks.values()):
            return JSONResponse(
                status_code=503, content={"status": "unhealthy", "checks": checks, "pipeline": pipeline}
            )
        return {"status": "ok", "checks": checks, "pipeline": pipeline}

    # Minimal operator UI: a single static, dependency-free HTML page
    # calling this same API from the browser (same-origin fetch, no CORS
    # config needed) — for running the pipeline without Swagger/curl.
    # Not part of the API surface, so it's excluded from the OpenAPI schema.
    _dashboard_path = Path(__file__).resolve().parent.parent / "static" / "dashboard.html"

    @app.get("/dashboard", include_in_schema=False)
    def dashboard_ui() -> FileResponse:
        return FileResponse(_dashboard_path)

    # Instrumented last (Production Hardening Sprint H6), after every other
    # route is registered — prometheus-fastapi-instrumentator's own
    # documented recommendation, so it sees the app's complete route table
    # when it exposes /metrics.
    configure_metrics(app, settings)

    return app


app = create_app()
