"""Human Review workflow (goal #6) and video listing — part of goal #8's
minimal dashboard/API surface."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from content_factory.api.deps import get_db
from content_factory.api.serializers import to_video_out
from content_factory.db.models.enums import VideoStatus
from content_factory.db.models.video import Video
from content_factory.schemas.review import ReviewDecisionOut, ReviewSubmitRequest
from content_factory.schemas.video import VideoOut
from content_factory.services import review_service

router = APIRouter(tags=["review"])


# NOTE: this literal route must be registered before "/videos/{video_id}" —
# Starlette matches routes in registration order, so a fixed path needs to
# come first or it would be swallowed by the parameterized route below.
@router.get("/videos/pending-review", response_model=list[VideoOut])
def list_pending_review(db: Session = Depends(get_db)) -> list[VideoOut]:
    videos = (
        db.query(Video)
        .filter(Video.status == VideoStatus.PENDING_REVIEW)
        .order_by(Video.created_at.asc())
        .all()
    )
    return [to_video_out(db, v) for v in videos]


@router.get("/videos", response_model=list[VideoOut])
def list_videos(db: Session = Depends(get_db)) -> list[VideoOut]:
    videos = db.query(Video).order_by(Video.created_at.desc()).all()
    return [to_video_out(db, v) for v in videos]


@router.get("/videos/{video_id}", response_model=VideoOut)
def get_video(video_id: int, db: Session = Depends(get_db)) -> VideoOut:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return to_video_out(db, video)


@router.post("/videos/{video_id}/review", response_model=ReviewDecisionOut)
def submit_review(video_id: int, payload: ReviewSubmitRequest, db: Session = Depends(get_db)) -> ReviewDecisionOut:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    decision = review_service.submit_review(
        db,
        video=video,
        reviewer_id=payload.reviewer_id,
        decision=payload.decision,
        reason_code=payload.reason_code,
        notes=payload.notes,
    )
    return ReviewDecisionOut.model_validate(decision)
