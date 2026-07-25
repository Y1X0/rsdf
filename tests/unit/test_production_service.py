from pathlib import Path

import pytest

from content_factory.db.models.campaign import Campaign
from content_factory.db.models.content import ContentIdea, Script
from content_factory.db.models.enums import ProcessingStatus, VideoStatus
from content_factory.db.models.video import Video
from content_factory.services import production_service


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
