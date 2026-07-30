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
    llm_effective = settings.resolved_llm_provider()
    tts_effective = settings.resolved_tts_provider()
    transcription_effective = settings.resolved_transcription_provider()
    notification_effective = settings.resolved_notification_provider()
    return SettingsStatusOut(
        environment=settings.environment,
        llm_provider_configured=settings.llm_provider,
        llm_provider_effective=llm_effective,
        llm_provider_using_fallback=llm_effective != settings.llm_provider,
        tts_provider_configured=settings.tts_provider,
        tts_provider_effective=tts_effective,
        tts_provider_using_fallback=tts_effective != settings.tts_provider,
        transcription_provider_configured=settings.transcription_provider,
        transcription_provider_effective=transcription_effective,
        transcription_provider_using_fallback=transcription_effective != settings.transcription_provider,
        notification_provider_configured=settings.notification_provider,
        notification_provider_effective=notification_effective,
        notification_provider_using_fallback=notification_effective != settings.notification_provider,
        renderer_backend=settings.renderer_backend,
        clip_renderer_backend=settings.clip_renderer_backend,
        publishing_enabled=settings.publishing_enabled,
        media_backup_enabled=settings.media_backup_enabled,
        rate_limit_backend=settings.rate_limit_backend,
    )
