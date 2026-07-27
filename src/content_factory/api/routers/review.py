"""Human Review workflow (goal #6) and video listing — part of goal #8's
minimal dashboard/API surface. Every route requires authentication;
submitting a review requires the operator role and always uses the
authenticated principal as the reviewer identity (PHASE1_AUDIT.md F2)."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from content_factory.api.deps import get_db
from content_factory.api.pagination import Pagination, pagination_params
from content_factory.api.serializers import to_video_out
from content_factory.auth.dependencies import require_auth, require_operator
from content_factory.config import Settings, get_settings
from content_factory.db.models.enums import ReviewDecisionType, VideoStatus
from content_factory.db.models.video import Video
from content_factory.logging_config import get_logger
from content_factory.schemas.review import ReviewDecisionOut, ReviewSubmitRequest
from content_factory.schemas.video import VideoOut
from content_factory.services import analytics_service, publishing_service, review_service

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


@router.get("/videos/{video_id}/file")
def download_video_file(
    video_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _principal: dict = Depends(require_auth),
) -> FileResponse:
    """Serves the actual rendered asset from local disk. `Video.asset_url`
    (set by whichever VideoRenderer produced it - see video_production/
    renderer/providers/*.py) has only ever been a raw server-local
    filesystem path, never a URL - there was no route anywhere that turned
    it into something a browser could actually open. `media_type` is left
    for FileResponse to guess from the extension (a real .mp4 renders
    inline; a NullRenderer's manifest .json downloads/opens as text - both
    honestly reflect what was actually produced, not a fabricated "video"
    the renderer never made)."""
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if not video.asset_url:
        raise HTTPException(status_code=404, detail="No rendered file exists for this video yet")

    asset_path = Path(video.asset_url).resolve()
    media_root = settings.media_storage_path().resolve()
    if not asset_path.is_relative_to(media_root):
        # asset_url is always server-generated, never taken from a request -
        # this is defense in depth, not a response to any real observed path
        raise HTTPException(status_code=404, detail="File not found")
    if not asset_path.is_file():
        raise HTTPException(status_code=404, detail="Rendered file is missing from disk")

    return FileResponse(asset_path, filename=asset_path.name)


@router.post("/videos/{video_id}/review", response_model=ReviewDecisionOut)
def submit_review(
    video_id: int,
    payload: ReviewSubmitRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
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
    result = ReviewDecisionOut.model_validate(decision)

    # The mandatory human gate is this review decision itself (goal #6);
    # everything after "approved" - publish, then metrics - proceeds
    # automatically from here with no further manual endpoint calls
    # needed, per the same "no stopping between stages" requirement the
    # Ideas -> Script -> Render cascade already closes. A rejection or
    # revision request stops the pipeline here, correctly - there is
    # nothing to auto-publish.
    if payload.decision == ReviewDecisionType.APPROVED:
        publish_outcome = publishing_service.attempt_auto_publish(db, video=video, settings=settings)
        result.auto_publish_status = publish_outcome.status
        result.auto_publish_detail = publish_outcome.detail
        logger.info(
            "auto_publish_cascade_result", video_id=video_id, status=publish_outcome.status,
            detail=publish_outcome.detail,
        )

        if publish_outcome.publication is not None:
            metrics_outcome = analytics_service.attempt_auto_metrics_sync(
                db, publication=publish_outcome.publication, settings=settings
            )
            result.auto_metrics_status = metrics_outcome.status
            result.auto_metrics_detail = metrics_outcome.detail
            logger.info(
                "auto_metrics_sync_cascade_result", video_id=video_id, status=metrics_outcome.status,
                detail=metrics_outcome.detail,
            )

    return result
