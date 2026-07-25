"""Analytics foundation (goal #7): views/retention/engagement/revenue/cost."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from content_factory.api.deps import get_db
from content_factory.db.models.video import Video
from content_factory.schemas.analytics import (
    CostSubmitRequest,
    MetricsResponse,
    MetricsSubmitRequest,
    ProfitSummaryOut,
    RevenueSubmitRequest,
    ViralScoreOut,
)
from content_factory.services import analytics_service

router = APIRouter(prefix="/videos", tags=["analytics"])


@router.post("/{video_id}/metrics", response_model=MetricsResponse)
def submit_metrics(video_id: int, payload: MetricsSubmitRequest, db: Session = Depends(get_db)) -> MetricsResponse:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    snapshot, score = analytics_service.record_metrics(
        db,
        video=video,
        views=payload.views,
        avg_watch_time_s=payload.avg_watch_time_s,
        completion_rate=payload.completion_rate,
        rewatch_rate=payload.rewatch_rate,
        shares=payload.shares,
        comments=payload.comments,
        likes=payload.likes,
        saves=payload.saves,
        source=payload.source,
    )
    return MetricsResponse(
        video_id=video_id,
        views=snapshot.views,
        captured_at=snapshot.captured_at,
        viral_score=ViralScoreOut.model_validate(score),
    )


@router.post("/{video_id}/cost")
def submit_cost(video_id: int, payload: CostSubmitRequest, db: Session = Depends(get_db)) -> dict:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    entry = analytics_service.record_manual_cost(
        db,
        video_id=video_id,
        category=payload.category,
        cost_usd=payload.cost_usd,
        provider=payload.provider,
        note=payload.note,
    )
    return {"id": entry.id, "cost_usd": float(entry.cost_usd)}


@router.post("/{video_id}/revenue")
def submit_revenue(video_id: int, payload: RevenueSubmitRequest, db: Session = Depends(get_db)) -> dict:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    snapshot = analytics_service.record_revenue(
        db,
        video=video,
        campaign_id=payload.campaign_id,
        raw_views=payload.raw_views,
        approved_views=payload.approved_views,
        payout_realized=payload.payout_realized,
        payout_pending=payload.payout_pending,
        status=payload.status,
    )
    return {"id": snapshot.id, "payout_realized": float(snapshot.payout_realized)}


@router.get("/{video_id}/profit", response_model=ProfitSummaryOut)
def get_profit(video_id: int, db: Session = Depends(get_db)) -> ProfitSummaryOut:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    summary = analytics_service.compute_profit_summary(db, video_id=video_id)
    return ProfitSummaryOut(**summary)
