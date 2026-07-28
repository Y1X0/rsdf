"""Clip factory pipeline (goal: long-form video -> AI-selected highlight
clips -> real, edited short-form footage): upload -> transcribe -> analyze
-> render. Mirrors production_service.py's shape (business logic depends
only on TranscriptionProvider/ClipRenderer/LLMClient interfaces, every
external call wrapped in agents.base.agent_run for the same versioning/
cost-ledger/logging guarantees), but a rendered clip is real, edited source
footage rather than something generated from scratch — so it skips
quality_scoring.score_video (originality/policy-risk scoring against a
Script's own generated text, which doesn't apply to real footage) in favor
of a much smaller structural QC check, and goes straight to
VideoStatus.PENDING_REVIEW: a human always reviews before publish either
way, regardless of which pipeline produced the Video row.
"""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from content_factory.agents.base import agent_run
from content_factory.agents.clip_selection_agent import ClipSelectionAgent
from content_factory.db.models.agent_run import AgentRun
from content_factory.db.models.clip import Clip
from content_factory.db.models.enums import ClipStatus, ProcessingStatus, VideoStatus
from content_factory.db.models.source_video import SourceVideo
from content_factory.db.models.video import Video
from content_factory.diarization.base import SpeakerDiarizationProvider, SpeakerTurn
from content_factory.llm.base import LLMClient
from content_factory.logging_config import get_logger
from content_factory.services.media_backup import (
    MediaBackupProvider,
    NullMediaBackupProvider,
    backup_and_get_public_url,
)
from content_factory.transcription.base import TranscriptionProvider, TranscriptSegment, TranscriptWord
from content_factory.video_clipping.base import ClipRenderer, ClipRenderRequest
from content_factory.video_clipping.scene_detection import detect_scene_changes
from content_factory.video_clipping.silence_trim import trim_leading_trailing_silence

logger = get_logger(__name__)

# Same tolerance philosophy as qc_service.DURATION_TOLERANCE_RATIO, applied
# to a real cut's own requested length rather than a script's target.
_CLIP_DURATION_TOLERANCE_S = 1.0


def register_source_video(
    db: Session, *, campaign_id: int | None, title: str, storage_path: str
) -> SourceVideo:
    source_video = SourceVideo(campaign_id=campaign_id, title=title, storage_path=storage_path)
    db.add(source_video)
    db.flush()
    logger.info("source_video_registered", source_video_id=source_video.id, storage_path=storage_path)
    return source_video


def transcribe_source_video(
    db: Session,
    *,
    source_video: SourceVideo,
    transcription_provider: TranscriptionProvider,
    diarization_provider: SpeakerDiarizationProvider | None = None,
) -> SourceVideo:
    log = logger.bind(source_video_id=source_video.id)
    source_video.transcription_status = ProcessingStatus.IN_PROGRESS
    db.flush()

    try:
        with agent_run(
            db,
            agent_name="transcription_provider",
            scope="source_video.transcribe",
            entity_type="source_video",
            entity_id=source_video.id,
            cost_campaign_id=source_video.campaign_id,
        ) as handle:
            result = transcription_provider.transcribe(source_video.storage_path)
            handle.record_output(
                provider=result.provider,
                model=result.model,
                model_version=None,
                prompt=source_video.storage_path,
                output_summary={"segment_count": len(result.segments), "duration_s": result.duration_s},
                cost_usd=result.cost_usd,
                duration_ms=result.duration_ms,
            )
        source_video.transcription_agent_run_id = handle.run.id
        source_video.transcript_text = result.text
        source_video.transcript_segments = [
            {"start": s.start_s, "end": s.end_s, "text": s.text} for s in result.segments
        ]
        source_video.transcript_words = [
            {"start": w.start_s, "end": w.end_s, "word": w.word} for w in result.words
        ]
        if result.duration_s:
            source_video.duration_s = result.duration_s
        source_video.transcription_status = ProcessingStatus.COMPLETED
        db.flush()
        log.info("source_video_transcribed", segment_count=len(result.segments))
    except Exception:
        # commit(), not flush(): api/deps.get_db() rolls back the whole
        # session when this exception reaches it, which would otherwise
        # silently wipe this status write and leave the row stuck showing
        # IN_PROGRESS forever - the exact same "P0" commit-boundary lesson
        # agent_run() itself already applies (see agents/base.py).
        source_video.transcription_status = ProcessingStatus.FAILED
        db.commit()
        log.error("source_video_transcription_failed", exc_info=True)
        raise

    # Diarization is genuinely optional and best-effort, deliberately
    # outside the try/except above: transcription itself already
    # succeeded by this point, so a diarization failure (real providers
    # like pyannote can fail in ways transcription never does - missing
    # model weights, an out-of-memory model load) must never be reported
    # as a transcription failure, and never blocks the pipeline.
    if diarization_provider is not None:
        try:
            # A SAVEPOINT (not the outer transaction): if diarization fails
            # mid-flush, only its own change is undone - a plain
            # db.rollback() here would also wipe out the transcription
            # success already flushed above, which must survive regardless
            # of whether this optional, best-effort step succeeds.
            with db.begin_nested():
                diarization_result = diarization_provider.diarize(source_video.storage_path)
                source_video.speaker_turns = [
                    {"start": t.start_s, "end": t.end_s, "speaker": t.speaker_label}
                    for t in diarization_result.turns
                ]
                db.flush()
            log.info("source_video_diarized", speaker_count=diarization_result.speaker_count)
        except Exception:
            log.warning("source_video_diarization_failed", exc_info=True)

    return source_video


def analyze_source_video(
    db: Session, *, source_video: SourceVideo, llm_client: LLMClient, max_clips: int = 5
) -> list[Clip]:
    log = logger.bind(source_video_id=source_video.id)
    source_video.analysis_status = ProcessingStatus.IN_PROGRESS
    db.flush()

    segments = [
        TranscriptSegment(start_s=s["start"], end_s=s["end"], text=s["text"])
        for s in (source_video.transcript_segments or [])
    ]

    scene_changes = detect_scene_changes(source_video.storage_path)

    try:
        agent = ClipSelectionAgent(llm_client)
        clips = agent.select_clips(
            db, source_video=source_video, segments=segments, max_clips=max_clips, scene_changes=scene_changes
        )
        # select_clips wraps its own agent_run internally (scope
        # "source_video.analyze"); link the most recent one here so
        # SourceVideo also carries a direct pointer to that run.
        latest_run = (
            db.query(AgentRun)
            .filter(
                AgentRun.entity_type == "source_video",
                AgentRun.entity_id == source_video.id,
                AgentRun.scope == "source_video.analyze",
            )
            .order_by(AgentRun.id.desc())
            .first()
        )
        if latest_run is not None:
            source_video.analysis_agent_run_id = latest_run.id
        source_video.analysis_status = ProcessingStatus.COMPLETED
        db.flush()
        log.info("source_video_analyzed", clip_count=len(clips))
    except Exception:
        # commit(), not flush() - same reasoning as transcribe_source_video's
        # own except block above.
        source_video.analysis_status = ProcessingStatus.FAILED
        db.commit()
        log.error("source_video_analysis_failed", exc_info=True)
        raise

    return clips


def _run_clip_qc(*, asset_url: str, requested_duration_s: float, actual_duration_s: float) -> tuple[str, str]:
    checks: list[str] = []
    asset_ok = Path(asset_url).exists() if not asset_url.startswith(("http://", "https://")) else True
    if not asset_ok:
        checks.append(f"rendered asset not found on disk ({asset_url!r})")

    duration_ok = abs(actual_duration_s - requested_duration_s) <= _CLIP_DURATION_TOLERANCE_S
    if not duration_ok:
        checks.append(
            f"rendered duration {actual_duration_s}s differs from the requested {requested_duration_s}s "
            f"by more than {_CLIP_DURATION_TOLERANCE_S}s"
        )

    passed = asset_ok and duration_ok
    return ("passed" if passed else "failed"), ("; ".join(checks) or "all automated checks passed")


def render_clip(
    db: Session,
    *,
    clip: Clip,
    source_video: SourceVideo,
    clip_renderer: ClipRenderer,
    media_backup_provider: MediaBackupProvider | None = None,
) -> Video:
    backup_provider = media_backup_provider or NullMediaBackupProvider()
    log = logger.bind(clip_id=clip.id, source_video_id=source_video.id)

    video = Video(clip_id=clip.id, status=VideoStatus.PENDING_RENDER)
    db.add(video)
    db.flush()
    video.render_status = ProcessingStatus.IN_PROGRESS
    video.render_requested_at = datetime.now(UTC)
    db.flush()

    segments = [
        TranscriptSegment(start_s=s["start"], end_s=s["end"], text=s["text"])
        for s in (source_video.transcript_segments or [])
    ]
    words = [
        TranscriptWord(start_s=w["start"], end_s=w["end"], word=w["word"])
        for w in (source_video.transcript_words or [])
    ]
    speaker_turns = [
        SpeakerTurn(start_s=t["start"], end_s=t["end"], speaker_label=t["speaker"])
        for t in (source_video.speaker_turns or [])
    ]

    # Trim dead air off both edges of the selected range before cutting -
    # a selection's exact boundary often lands a fraction of a second
    # before speech starts or after it ends. clip.start_s/end_s themselves
    # (the LLM's own selection, already persisted) are left untouched;
    # only the actual render/QC target range is adjusted.
    render_start_s, render_end_s = trim_leading_trailing_silence(
        source_video.storage_path, start_s=clip.start_s, end_s=clip.end_s
    )

    try:
        with agent_run(
            db,
            agent_name="clip_renderer",
            scope="clip.render",
            entity_type="video",
            entity_id=video.id,
            cost_video_id=video.id,
            cost_campaign_id=source_video.campaign_id,
        ) as handle:
            request = ClipRenderRequest(
                clip_id=clip.id,
                source_path=source_video.storage_path,
                start_s=render_start_s,
                end_s=render_end_s,
                hook_text=clip.hook_text,
                transcript_segments=segments,
                transcript_words=words,
                speaker_turns=speaker_turns,
            )
            result = clip_renderer.render(request)
            handle.record_output(
                provider=result.provider,
                model=None,
                model_version=None,
                prompt=f"clip {clip.id}: {clip.start_s}-{clip.end_s}s of source_video {source_video.id}",
                output_summary={"asset_url": result.asset_url},
                cost_usd=0.0,
                duration_ms=result.duration_ms,
            )
        video.render_agent_run_id = handle.run.id
        public_url = backup_and_get_public_url(backup_provider, result.asset_url, log=log)
        # Same rule as production_service.render_video: a public URL (real
        # object storage configured and upload succeeded) replaces the
        # local path outright, since publishing_service reads Video.asset_url
        # directly and a platform can never reach a local filesystem path.
        video.asset_url = public_url or result.asset_url
        video.thumbnail_url = result.thumbnail_url
        video.duration_s = result.duration_s
        video.caption_style = "clip_subtitles"
        # Real source footage and real source audio - neither the voice nor
        # the visual content is AI-generated (only text overlays are added),
        # unlike the Script pipeline's template-rendered/TTS-voiced videos.
        video.contains_ai_voice = False
        video.contains_ai_visual = False
        video.render_status = ProcessingStatus.COMPLETED
        video.render_completed_at = datetime.now(UTC)
        video.status = VideoStatus.PENDING_REVIEW

        qc_status, qc_notes = _run_clip_qc(
            asset_url=result.asset_url,
            # The renderer was actually asked to cut render_start_s..
            # render_end_s (post-silence-trim), not clip.start_s..end_s -
            # QC must compare against what was actually requested, or a
            # trimmed clip would spuriously fail the duration check.
            requested_duration_s=render_end_s - render_start_s,
            actual_duration_s=result.duration_s,
        )
        video.qc_status = qc_status
        video.qc_notes = qc_notes
        db.flush()

        clip.status = ClipStatus.RENDERED
        db.flush()

        log.info("clip_rendered", asset_url=result.asset_url, qc_status=qc_status)
        if qc_status != "passed":
            log.warning("clip_automated_qc_failed", notes=qc_notes)
    except Exception:
        # commit(), not flush() - same reasoning as transcribe_source_video's
        # own except block above. In practice this path is always reached
        # through idempotency.run_idempotent(), whose own except block
        # already commits regardless - but committing here too removes the
        # implicit dependency on that caller behavior, matching the other
        # two functions in this module for the same reason.
        video.render_status = ProcessingStatus.FAILED
        video.status = VideoStatus.RENDER_FAILED
        video.render_completed_at = datetime.now(UTC)
        db.commit()
        log.error("clip_render_failed", exc_info=True)
        raise

    return video
