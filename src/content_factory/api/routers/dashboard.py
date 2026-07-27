"""Minimal dashboard/API (goal #8): a single rollup endpoint covering
campaigns, generated content, review status, and performance. Combined with
FastAPI's built-in interactive docs (see docs/PHASE1.md), this is the
Phase 1 "even a spreadsheet-backed one is fine" dashboard from
ARCHITECTURE.md §22 — a full Next.js dashboard is Phase 2+."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from content_factory.api.deps import get_db
from content_factory.auth.dependencies import require_auth
from content_factory.config import Settings, get_settings
from content_factory.schemas.dashboard import DashboardSummaryOut, SettingsStatusOut
from content_factory.services import analytics_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummaryOut)
def dashboard_summary(
    db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> DashboardSummaryOut:
    summary = analytics_service.get_dashboard_summary(db)
    return DashboardSummaryOut(**summary)


@router.get("/settings", response_model=SettingsStatusOut)
def dashboard_settings(
    settings: Settings = Depends(get_settings), _principal: dict = Depends(require_auth)
) -> SettingsStatusOut:
    return SettingsStatusOut(
        environment=settings.environment,
        llm_provider=settings.llm_provider,
        tts_provider=settings.tts_provider,
        renderer_backend=settings.renderer_backend,
        clip_renderer_backend=settings.clip_renderer_backend,
        transcription_provider=settings.transcription_provider,
        notification_provider=settings.notification_provider,
        publishing_enabled=settings.publishing_enabled,
        media_backup_enabled=settings.media_backup_enabled,
        rate_limit_backend=settings.rate_limit_backend,
    )
