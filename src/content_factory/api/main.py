from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from content_factory.api.routers import (
    accounts,
    analytics,
    auth,
    budget,
    campaigns,
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
from content_factory.services.budget_governor import BudgetExceeded
from content_factory.services.idempotency import IdempotencyConflict, IdempotencyInProgress

logger = get_logger(__name__)


def create_app() -> FastAPI:
    configure_logging()
    # Production Hardening Sprint H1: boot-time fail-closed check — see
    # Settings.validate_production_safety's own docstring for why this
    # exists. A no-op unless ENVIRONMENT=production is explicitly set.
    get_settings().validate_production_safety()

    app = FastAPI(
        title="AI Content Factory",
        description="Whop Content Rewards content pipeline. See docs/ARCHITECTURE.md and docs/PHASE1.md.",
        version="2.0.0",
    )

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
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            logger.error("health_check_db_failed", exc_info=True)
            return JSONResponse(status_code=503, content={"status": "unhealthy", "reason": "database unreachable"})
        return {"status": "ok"}

    return app


app = create_app()
