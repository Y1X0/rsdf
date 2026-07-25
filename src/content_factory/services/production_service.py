"""Production pipeline (goal #5): script -> TTS -> captions -> template
renderer -> Video row. Business logic here depends only on the TTSProvider
and VideoRenderer interfaces (adjustment #2/#6) — never on ElevenLabs,
Pillow, ffmpeg, or any other concrete provider. Both external calls are
wrapped in agents.base.agent_run so they get the same versioning/logging
guarantees as the Research/Script agents (adjustments #3/#4); passing
`cost_video_id`/`cost_campaign_id` to agent_run means its CostLedger entry
and the AgentRun's own COMPLETED/FAILED status commit together, immediately,
the moment each step finishes — see agents/base.py's v1.1 durability note
(PHASE1_AUDIT.md F1) for why that matters here specifically: this function
runs *two* agent_run() calls per video, and the old flush-only behavior let
a render failure erase the record of an already-successful, already-billed
TTS call.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from content_factory.agents.base import agent_run
from content_factory.db.models.content import Script
from content_factory.db.models.enums import ProcessingStatus, VideoStatus
from content_factory.db.models.video import Video
from content_factory.logging_config import get_logger
from content_factory.services import qc_service
from content_factory.services.media_backup import MediaBackupProvider, NullMediaBackupProvider
from content_factory.video_production.captions import build_captions
from content_factory.video_production.renderer.base import RenderRequest, VideoRenderer
from content_factory.video_production.tts.base import TTSProvider

logger = get_logger(__name__)

DEFAULT_TEMPLATE_ID = "default_template_v1"
DEFAULT_VOICE_ID = "default"


def _backup_if_local(backup_provider: MediaBackupProvider, path: str | None, *, log) -> None:
    """Best-effort, never fatal (Production Hardening Sprint H3, DR4) —
    a failed or skipped backup must never fail an otherwise-successful
    render. Skips remote-URL assets (nothing local to copy), matching
    qc_service's existing check for the same case."""
    if not path or path.startswith(("http://", "https://")):
        return
    try:
        result = backup_provider.backup(path)
        if result.backed_up:
            log.info("media_backed_up", local_path=path, location=result.location)
    except Exception:
        log.error("media_backup_unexpected_error", local_path=path, exc_info=True)


def render_video(
    db: Session,
    *,
    video: Video,
    script: Script,
    tts_provider: TTSProvider,
    video_renderer: VideoRenderer,
    template_id: str = DEFAULT_TEMPLATE_ID,
    media_backup_provider: MediaBackupProvider | None = None,
) -> Video:
    backup_provider = media_backup_provider or NullMediaBackupProvider()
    video.render_status = ProcessingStatus.IN_PROGRESS
    video.render_requested_at = datetime.now(UTC)
    db.flush()

    log = logger.bind(video_id=video.id, script_id=script.id)
    log.info("production_started")

    campaign_id = script.idea.campaign_id if script.idea else None

    try:
        with agent_run(
            db,
            agent_name="tts_provider",
            scope="video.render.tts",
            entity_type="video",
            entity_id=video.id,
            cost_video_id=video.id,
            cost_campaign_id=campaign_id,
        ) as tts_handle:
            tts_result = tts_provider.synthesize(text=script.full_text, voice_id=DEFAULT_VOICE_ID)
            tts_handle.record_output(
                provider=tts_result.provider,
                model=tts_result.model,
                model_version=tts_result.model_version,
                prompt=script.full_text,
                output_summary={"duration_s": tts_result.duration_s, "audio_path": tts_result.audio_path},
                cost_usd=tts_result.cost_usd,
                duration_ms=tts_result.duration_ms,
            )
        # Recorded immediately (not deferred to the end of the function)
        # so the video row itself reflects which TTS run serviced it even
        # if a later step (the renderer) fails — otherwise the durability
        # fix above would preserve the AgentRun/CostLedger rows, but the
        # Video row would still have no way to point back at them.
        video.tts_agent_run_id = tts_handle.run.id
        db.flush()
        _backup_if_local(backup_provider, tts_result.audio_path, log=log)

        captions = build_captions(tts_result.word_timings)

        with agent_run(
            db,
            agent_name="video_renderer",
            scope="video.render.render",
            entity_type="video",
            entity_id=video.id,
            cost_video_id=video.id,
            cost_campaign_id=campaign_id,
        ) as render_handle:
            render_request = RenderRequest(
                video_id=video.id,
                template_id=template_id,
                hook_text=script.hook_text,
                script_text=script.full_text,
                voiceover_audio_path=tts_result.audio_path,
                captions=captions,
                target_duration_s=tts_result.duration_s,
            )
            render_result = video_renderer.render(render_request)
            render_handle.record_output(
                provider=render_result.provider,
                model=render_result.model,
                model_version=render_result.model_version,
                prompt=script.full_text,
                output_summary={"asset_url": render_result.asset_url},
                cost_usd=render_result.cost_usd,
                duration_ms=render_result.duration_ms,
            )
        video.render_agent_run_id = render_handle.run.id
        db.flush()
        _backup_if_local(backup_provider, render_result.asset_url, log=log)

        video.asset_url = render_result.asset_url
        video.thumbnail_url = render_result.thumbnail_url
        video.duration_s = render_result.duration_s
        video.template_id = template_id
        video.voice_id = DEFAULT_VOICE_ID
        video.caption_style = "default"
        # "silent" is a placeholder with no real audio at all, so it does not
        # trigger an AI-voice disclosure; any real synthesis provider does.
        video.contains_ai_voice = tts_result.provider not in ("silent",)
        video.contains_ai_visual = render_result.provider not in ("null",)
        video.render_status = ProcessingStatus.COMPLETED
        video.render_completed_at = datetime.now(UTC)
        video.status = VideoStatus.RENDERED

        qc_result = qc_service.run_automated_qc(
            script=script, tts_result=tts_result, render_result=render_result, captions=captions
        )
        video.qc_status = "passed" if qc_result.passed else "failed"
        video.qc_notes = qc_result.notes
        db.flush()

        log.info(
            "production_completed",
            asset_url=render_result.asset_url,
            duration_s=render_result.duration_s,
            qc_status=video.qc_status,
        )
        if not qc_result.passed:
            log.warning("automated_qc_failed", checks=qc_result.checks, notes=qc_result.notes)
    except Exception:
        video.render_status = ProcessingStatus.FAILED
        video.status = VideoStatus.RENDER_FAILED
        video.render_completed_at = datetime.now(UTC)
        db.flush()
        log.error("production_failed", exc_info=True)
        raise

    return video
