"""Human Review workflow (goal #6) and video listing — part of goal #8's
minimal dashboard/API surface. Every route requires authentication;
submitting a review requires the operator role and always uses the
authenticated principal as the reviewer identity (PHASE1_AUDIT.md F2)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from content_factory.api.deps import get_db
from content_factory.api.pagination import Pagination, pagination_params
from content_factory.api.serializers import to_video_out
from content_factory.auth.dependencies import require_auth, require_operator
from content_factory.db.models.enums import VideoStatus
from content_factory.db.models.video import Video
from content_factory.logging_config import get_logger
from content_factory.schemas.review import ReviewDecisionOut, ReviewSubmitRequest
from content_factory.schemas.video import VideoOut
from content_factory.services import review_service

logger = get_logger(__name__)
router = APIRouter(tags=["review"])


# NOTE: this literal route must be registered before "/videos/{video_id}" —
# Starlette matches routes in registration order, so a fixed path needs to
# come first or it would be swallowed by the parameterized route below.
@router.get("/videos/pending-review", response_model=list[VideoOut])
def list_pending_review(
    db: Session = Depends(get_db),
    pagination: Pagination = Depends(pagination_params),
    _principal: dict = Depends(require_auth),
) -> list[VideoOut]:
    videos = (
        db.query(Video)
        .filter(Video.status == VideoStatus.PENDING_REVIEW)
        .order_by(Video.created_at.asc())
        .offset(pagination.offset)
        .limit(pagination.limit)
        .all()
    )
    return [to_video_out(db, v) for v in videos]


@router.get("/videos", response_model=list[VideoOut])
def list_videos(
    db: Session = Depends(get_db),
    pagination: Pagination = Depends(pagination_params),
    _principal: dict = Depends(require_auth),
) -> list[VideoOut]:
    videos = (
        db.query(Video)
        .order_by(Video.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
        .all()
    )
    return [to_video_out(db, v) for v in videos]


@router.get("/videos/{video_id}", response_model=VideoOut)
def get_video(
    video_id: int, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> VideoOut:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return to_video_out(db, video)


@router.post("/videos/{video_id}/review", response_model=ReviewDecisionOut)
def submit_review(
    video_id: int,
    payload: ReviewSubmitRequest,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_operator),
) -> ReviewDecisionOut:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    authenticated_reviewer_id = principal["sub"]
    if payload.reviewer_id and payload.reviewer_id != authenticated_reviewer_id:
        logger.warning(
            "review_reviewer_id_mismatch_ignored",
            video_id=video_id,
            supplied=payload.reviewer_id,
            authenticated=authenticated_reviewer_id,
        )

    decision = review_service.submit_review(
        db,
        video=video,
        reviewer_id=authenticated_reviewer_id,
        decision=payload.decision,
        reason_code=payload.reason_code,
        notes=payload.notes,
    )
    return ReviewDecisionOut.model_validate(decision)
