from pathlib import Path

import pytest

from content_factory.db.models.campaign import Campaign
from content_factory.db.models.content import ContentIdea, Script
from content_factory.db.models.enums import ProcessingStatus, VideoStatus
from content_factory.db.models.video import Video
from content_factory.services import production_service
from content_factory.services.media_backup import MediaBackupProvider, MediaBackupResult


def _make_video_and_script(db_session) -> tuple[Video, Script]:
    campaign = Campaign(brand_name="Acme")
    db_session.add(campaign)
    db_session.flush()
    idea = ContentIdea(campaign_id=campaign.id, concept_summary="idea")
    db_session.add(idea)
    db_session.flush()
    script = Script(
        idea_id=idea.id, variant_label="v1", hook_text="hook", full_text="a full script about saving money",
        generation_status=ProcessingStatus.COMPLETED,
    )
    db_session.add(script)
    db_session.flush()
    video = Video(script_id=script.id, status=VideoStatus.PENDING_RENDER)
    db_session.add(video)
    db_session.flush()
    return video, script


def test_render_video_completes_pipeline_and_records_costs(db_session, silent_tts_provider, null_video_renderer):
    video, script = _make_video_and_script(db_session)

    result = production_service.render_video(
        db_session, video=video, script=script, tts_provider=silent_tts_provider, video_renderer=null_video_renderer
    )

    assert result.render_status == ProcessingStatus.COMPLETED
    assert result.status == VideoStatus.RENDERED
    assert result.asset_url is not None
    assert result.duration_s is not None
    assert result.tts_agent_run_id is not None
    assert result.render_agent_run_id is not None
    assert result.contains_ai_voice is False  # silent provider carries no real synthesized voice
    assert result.contains_ai_visual is False  # null renderer produces no real synthetic visuals
    assert Path(result.asset_url).exists()


def test_render_video_marks_failed_on_renderer_error(db_session, silent_tts_provider):
    class _BoomRenderer:
        def render(self, request):
            raise RuntimeError("renderer exploded")

    video, script = _make_video_and_script(db_session)

    with pytest.raises(RuntimeError):
        production_service.render_video(
            db_session, video=video, script=script, tts_provider=silent_tts_provider, video_renderer=_BoomRenderer()
        )

    assert video.render_status == ProcessingStatus.FAILED
    assert video.status == VideoStatus.RENDER_FAILED


def test_render_video_calls_media_backup_for_both_assets(db_session, silent_tts_provider, null_video_renderer):
    """Production Hardening Sprint H3 (DR4): both the TTS audio and the
    rendered video asset get offered to the backup provider."""
    video, script = _make_video_and_script(db_session)
    backed_up_paths = []

    class _RecordingBackupProvider(MediaBackupProvider):
        def backup(self, local_path: str) -> MediaBackupResult:
            backed_up_paths.append(local_path)
            return MediaBackupResult(backed_up=True, location=f"s3://bucket/{local_path}")

    result = production_service.render_video(
        db_session,
        video=video,
        script=script,
        tts_provider=silent_tts_provider,
        video_renderer=null_video_renderer,
        media_backup_provider=_RecordingBackupProvider(),
    )

    assert len(backed_up_paths) == 2
    assert any(p for p in backed_up_paths if "audio" in p or p.endswith(".wav"))
    assert result.asset_url in backed_up_paths


def test_render_video_succeeds_even_if_backup_provider_raises(db_session, silent_tts_provider, null_video_renderer):
    """Backup is best-effort and must never fail an otherwise-successful
    render (Production Hardening Sprint H3)."""
    video, script = _make_video_and_script(db_session)

    class _BoomBackupProvider(MediaBackupProvider):
        def backup(self, local_path: str) -> MediaBackupResult:
            raise RuntimeError("backup exploded")

    result = production_service.render_video(
        db_session,
        video=video,
        script=script,
        tts_provider=silent_tts_provider,
        video_renderer=null_video_renderer,
        media_backup_provider=_BoomBackupProvider(),
    )

    assert result.render_status == ProcessingStatus.COMPLETED
    assert result.status == VideoStatus.RENDERED


def test_render_video_replaces_asset_url_with_public_url_when_backup_provides_one(
    db_session, silent_tts_provider, null_video_renderer
):
    """This is what actually closes the profit loop's storage blocker:
    Video.asset_url must become the real public URL, since
    publishing_service.py reads that field directly and a platform can
    never reach a local filesystem path."""
    video, script = _make_video_and_script(db_session)

    class _PubliclyHostedBackupProvider(MediaBackupProvider):
        def backup(self, local_path: str) -> MediaBackupResult:
            return MediaBackupResult(
                backed_up=True, location=f"s3://bucket/{local_path}", public_url=f"https://cdn.test/{local_path}"
            )

    result = production_service.render_video(
        db_session,
        video=video,
        script=script,
        tts_provider=silent_tts_provider,
        video_renderer=null_video_renderer,
        media_backup_provider=_PubliclyHostedBackupProvider(),
    )

    assert result.asset_url.startswith("https://cdn.test/")


def test_render_video_defaults_to_null_backup_provider_when_omitted(
    db_session, silent_tts_provider, null_video_renderer
):
    """Existing callers (and every test written before this sprint) that
    don't pass media_backup_provider at all must keep working unchanged."""
    video, script = _make_video_and_script(db_session)

    result = production_service.render_video(
        db_session, video=video, script=script, tts_provider=silent_tts_provider, video_renderer=null_video_renderer
    )

    assert result.render_status == ProcessingStatus.COMPLETED
