"""Clip factory (montage pipeline, goal: turn a real long-form video into
AI-selected, real-footage short clips): upload -> transcribe -> analyze ->
render. Every mutating route requires the operator role (matching every
other router); transcribe/analyze/render all invoke external providers, so
each enforces the budget ceiling first, exactly like campaigns/content.py's
research/render routes.
"""

import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from content_factory.api.deps import (
    get_clip_renderer,
    get_db,
    get_diarization_provider,
    get_llm_client,
    get_media_backup_provider,
    get_notification_provider,
    get_transcription_provider,
)
from content_factory.api.pagination import Pagination, pagination_params
from content_factory.api.serializers import to_video_out
from content_factory.auth.dependencies import require_auth, require_operator
from content_factory.config import get_settings
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.clip import Clip
from content_factory.db.models.source_video import SourceVideo
from content_factory.db.models.video import Video
from content_factory.diarization.base import SpeakerDiarizationProvider
from content_factory.llm.base import LLMClient
from content_factory.logging_config import get_logger
from content_factory.schemas.clip import ClipOut, ClipRenderRequestBody
from content_factory.schemas.source_video import AnalyzeRequest, SourceVideoOut, TranscribeRequest
from content_factory.schemas.video import VideoOut
from content_factory.services import clip_service, content_intelligence, idempotency
from content_factory.services.budget_governor import enforce_budget
from content_factory.services.media_backup import MediaBackupProvider
from content_factory.transcription.base import TranscriptionProvider
from content_factory.video_clipping.base import ClipRenderer

logger = get_logger(__name__)
router = APIRouter(tags=["clips"])

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _to_clip_out(clip: Clip) -> ClipOut:
    return ClipOut(
        id=clip.id,
        source_video_id=clip.source_video_id,
        start_s=clip.start_s,
        end_s=clip.end_s,
        hook_text=clip.hook_text,
        hook_framework=clip.hook_framework,
        hook_strength_score=clip.hook_strength_score,
        predicted_score=clip.predicted_score,
        reason=clip.reason,
        status=clip.status,
        video_id=clip.video.id if clip.video else None,
        created_at=clip.created_at,
    )


def _niche_id_for_campaign(db: Session, campaign_id: int | None) -> int | None:
    if campaign_id is None:
        return None
    campaign = db.get(Campaign, campaign_id)
    return campaign.niche_id if campaign else None


def _get_source_video_or_404(db: Session, source_video_id: int) -> SourceVideo:
    source_video = db.get(SourceVideo, source_video_id)
    if source_video is None:
        raise HTTPException(status_code=404, detail="Source video not found")
    return source_video


@router.post("/source-videos", response_model=SourceVideoOut)
def upload_source_video(
    title: str = Form(..., max_length=300),
    campaign_id: int | None = Form(default=None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _principal: dict = Depends(require_operator),
) -> SourceVideoOut:
    settings = get_settings()
    storage_dir = settings.media_storage_path() / "source_videos"
    storage_dir.mkdir(parents=True, exist_ok=True)

    source_video = clip_service.register_source_video(
        db, campaign_id=campaign_id, title=title, storage_path=""
    )

    safe_name = _UNSAFE_FILENAME_CHARS.sub("_", file.filename or "upload.mp4")
    dest_path = storage_dir / f"{source_video.id}_{safe_name}"
    with dest_path.open("wb") as out:
        while chunk := file.file.read(1024 * 1024):
            out.write(chunk)
    source_video.storage_path = str(dest_path)
    db.flush()

    return SourceVideoOut.model_validate(source_video)


@router.get("/source-videos", response_model=list[SourceVideoOut])
def list_source_videos(
    db: Session = Depends(get_db),
    pagination: Pagination = Depends(pagination_params),
    _principal: dict = Depends(require_auth),
) -> list[SourceVideoOut]:
    rows = (
        db.query(SourceVideo)
        .order_by(SourceVideo.id.desc())
        .limit(pagination.limit)
        .offset(pagination.offset)
        .all()
    )
    return [SourceVideoOut.model_validate(r) for r in rows]


@router.get("/source-videos/{source_video_id}", response_model=SourceVideoOut)
def get_source_video(
    source_video_id: int, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> SourceVideoOut:
    return SourceVideoOut.model_validate(_get_source_video_or_404(db, source_video_id))


@router.post("/source-videos/{source_video_id}/transcribe", response_model=SourceVideoOut)
def transcribe_source_video(
    source_video_id: int,
    payload: TranscribeRequest,
    db: Session = Depends(get_db),
    transcription_provider: TranscriptionProvider = Depends(get_transcription_provider),
    diarization_provider: SpeakerDiarizationProvider = Depends(get_diarization_provider),
    notification_provider=Depends(get_notification_provider),
    _principal: dict = Depends(require_operator),
) -> SourceVideoOut:
    source_video = _get_source_video_or_404(db, source_video_id)
    enforce_budget(
        db,
        niche_id=_niche_id_for_campaign(db, source_video.campaign_id),
        notification_provider=notification_provider,
    )
    clip_service.transcribe_source_video(
        db,
        source_video=source_video,
        transcription_provider=transcription_provider,
        diarization_provider=diarization_provider,
    )
    return SourceVideoOut.model_validate(source_video)


@router.post("/source-videos/{source_video_id}/analyze", response_model=list[ClipOut])
def analyze_source_video(
    source_video_id: int,
    payload: AnalyzeRequest,
    db: Session = Depends(get_db),
    llm_client: LLMClient = Depends(get_llm_client),
    notification_provider=Depends(get_notification_provider),
    _principal: dict = Depends(require_operator),
) -> list[ClipOut]:
    source_video = _get_source_video_or_404(db, source_video_id)
    niche_id = _niche_id_for_campaign(db, source_video.campaign_id)
    enforce_budget(db, niche_id=niche_id, notification_provider=notification_provider)
    clips = clip_service.analyze_source_video(
        db, source_video=source_video, llm_client=llm_client, max_clips=payload.max_clips
    )
    # Same treatment as the Script pipeline's own hook tracking
    # (api/routers/content.py's _generate_scripts_for_idea): every hook a
    # clip actually gets is recorded into the shared HookLibrary learning
    # loop, so clip-factory hooks feed the same get_top_hooks retrieval
    # and eventual real-outcome tracking as script hooks do - previously
    # clip hooks never reached this system at all.
    for clip in clips:
        if clip.hook_text:
            content_intelligence.record_hook_usage(
                db, niche_id=niche_id, hook_text=clip.hook_text, hook_type=clip.hook_framework
            )
    return [_to_clip_out(c) for c in clips]


@router.get("/source-videos/{source_video_id}/clips", response_model=list[ClipOut])
def list_clips(
    source_video_id: int, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> list[ClipOut]:
    _get_source_video_or_404(db, source_video_id)
    rows = db.query(Clip).filter(Clip.source_video_id == source_video_id).order_by(Clip.id).all()
    return [_to_clip_out(c) for c in rows]


@router.post("/clips/{clip_id}/render", response_model=VideoOut)
def render_clip(
    clip_id: int,
    payload: ClipRenderRequestBody,
    db: Session = Depends(get_db),
    clip_renderer: ClipRenderer = Depends(get_clip_renderer),
    notification_provider=Depends(get_notification_provider),
    media_backup_provider: MediaBackupProvider = Depends(get_media_backup_provider),
    _principal: dict = Depends(require_operator),
) -> VideoOut:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    source_video = clip.source_video

    enforce_budget(
        db,
        niche_id=_niche_id_for_campaign(db, source_video.campaign_id),
        notification_provider=notification_provider,
    )

    def _load_existing(video_id: int) -> Video:
        return db.get(Video, video_id)

    def _work():
        return clip_service.render_clip(
            db,
            clip=clip,
            source_video=source_video,
            clip_renderer=clip_renderer,
            media_backup_provider=media_backup_provider,
        )

    try:
        video, _created = idempotency.run_idempotent(
            db,
            scope="clip.render",
            idempotency_key=payload.idempotency_key,
            payload={"clip_id": clip_id},
            entity_type="video",
            work_fn=_work,
            load_existing=_load_existing,
        )
    except clip_service.ClipAlreadyRendered as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return to_video_out(db, video)
