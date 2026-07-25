"""Minimal dashboard/API (goal #8): a single rollup endpoint covering
campaigns, generated content, review status, and performance. Combined with
FastAPI's built-in interactive docs (see docs/PHASE1.md), this is the
Phase 1 "even a spreadsheet-backed one is fine" dashboard from
ARCHITECTURE.md §22 — a full Next.js dashboard is Phase 2+."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from content_factory.api.deps import get_db
from content_factory.schemas.dashboard import DashboardSummaryOut
from content_factory.services import analytics_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummaryOut)
def dashboard_summary(db: Session = Depends(get_db)) -> DashboardSummaryOut:
    summary = analytics_service.get_dashboard_summary(db)
    return DashboardSummaryOut(**summary)
