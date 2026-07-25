from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from content_factory.api.routers import analytics, campaigns, content, dashboard, review
from content_factory.logging_config import configure_logging
from content_factory.services.idempotency import IdempotencyConflict, IdempotencyInProgress


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="AI Content Factory — Phase 1 MVP",
        description="Whop Content Rewards content pipeline. See docs/ARCHITECTURE.md and docs/PHASE1.md.",
        version="0.1.0",
    )

    app.include_router(campaigns.router)
    app.include_router(content.router)
    app.include_router(review.router)
    app.include_router(analytics.router)
    app.include_router(dashboard.router)

    @app.exception_handler(IdempotencyConflict)
    def _handle_idempotency_conflict(request: Request, exc: IdempotencyConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(IdempotencyInProgress)
    def _handle_idempotency_in_progress(request: Request, exc: IdempotencyInProgress) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health", tags=["health"])
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
