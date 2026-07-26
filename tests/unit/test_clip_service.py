import json

import pytest

from content_factory.db.models.enums import ClipStatus, ProcessingStatus, VideoStatus
from content_factory.llm.providers.fake_provider import FakeLLMClient
from content_factory.services import clip_service
from content_factory.transcription.base import TranscriptionResult, TranscriptSegment
from content_factory.video_clipping.base import ClipRenderResult
from content_factory.video_clipping.providers.null_clip_renderer import NullClipRenderer


class _FakeTranscriptionProvider:
    def __init__(self, result: TranscriptionResult) -> None:
        self._result = result

    def transcribe(self, audio_path: str) -> TranscriptionResult:
        return self._result


def test_register_source_video_creates_row(db_session):
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    assert source_video.id is not None
    assert source_video.transcription_status == ProcessingStatus.PENDING
    assert source_video.analysis_status == ProcessingStatus.PENDING


def test_transcribe_source_video_populates_transcript_and_segments(db_session):
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    provider = _FakeTranscriptionProvider(
        TranscriptionResult(
            text="hello world",
            segments=[TranscriptSegment(start_s=0.0, end_s=3.0, text="hello world")],
            provider="fake",
            model="fake-model",
            duration_s=3.0,
        )
    )

    result = clip_service.transcribe_source_video(db_session, source_video=source_video, transcription_provider=provider)

    assert result.transcription_status == ProcessingStatus.COMPLETED
    assert result.transcript_text == "hello world"
    assert result.transcript_segments == [{"start": 0.0, "end": 3.0, "text": "hello world"}]
    assert result.duration_s == 3.0
    assert result.transcription_agent_run_id is not None


def test_transcribe_source_video_marks_failed_on_error(db_session):
    class _BoomProvider:
        def transcribe(self, audio_path):
            raise RuntimeError("provider down")

    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    with pytest.raises(RuntimeError):
        clip_service.transcribe_source_video(db_session, source_video=source_video, transcription_provider=_BoomProvider())
    assert source_video.transcription_status == ProcessingStatus.FAILED


def test_analyze_source_video_creates_clips_from_transcript(db_session):
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    source_video.transcript_segments = [{"start": 0.0, "end": 10.0, "text": "an interesting moment happens here"}]
    db_session.flush()

    canned = [{"start_s": 1.0, "end_s": 8.0, "hook_text": "hook", "predicted_score": 0.8, "reason": "why"}]
    llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))

    clips = clip_service.analyze_source_video(db_session, source_video=source_video, llm_client=llm, max_clips=5)

    assert len(clips) == 1
    assert source_video.analysis_status == ProcessingStatus.COMPLETED
    assert source_video.analysis_agent_run_id is not None


def test_render_clip_creates_video_row_reusing_existing_pipeline_fields(db_session, tmp_media_dir):
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    source_video.transcript_segments = [{"start": 0.0, "end": 10.0, "text": "hello"}]
    db_session.flush()

    from content_factory.db.models.clip import Clip

    clip = Clip(source_video_id=source_video.id, start_s=1.0, end_s=6.0, hook_text="hook", status=ClipStatus.SUGGESTED)
    db_session.add(clip)
    db_session.flush()

    renderer = NullClipRenderer(storage_dir=tmp_media_dir / "clips")
    video = clip_service.render_clip(db_session, clip=clip, source_video=source_video, clip_renderer=renderer)

    assert video.clip_id == clip.id
    assert video.script_id is None
    assert video.status == VideoStatus.PENDING_REVIEW
    assert video.render_status == ProcessingStatus.COMPLETED
    assert video.contains_ai_voice is False
    assert video.contains_ai_visual is False
    assert video.qc_status == "passed"
    assert clip.status == ClipStatus.RENDERED


def test_render_clip_marks_failed_on_renderer_error(db_session, tmp_media_dir):
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    from content_factory.db.models.clip import Clip

    clip = Clip(source_video_id=source_video.id, start_s=1.0, end_s=6.0, status=ClipStatus.SUGGESTED)
    db_session.add(clip)
    db_session.flush()

    class _BoomRenderer:
        def render(self, request):
            raise RuntimeError("renderer exploded")

    with pytest.raises(RuntimeError):
        clip_service.render_clip(db_session, clip=clip, source_video=source_video, clip_renderer=_BoomRenderer())

    from content_factory.db.models.video import Video

    video = db_session.query(Video).filter(Video.clip_id == clip.id).one()
    assert video.render_status == ProcessingStatus.FAILED
    assert video.status == VideoStatus.RENDER_FAILED


def test_render_clip_flags_qc_failed_when_duration_mismatches(db_session, tmp_media_dir):
    """A renderer that lies about its own output duration must be caught,
    not silently trusted - same spirit as qc_service's existing duration
    check for the Script pipeline."""
    source_video = clip_service.register_source_video(
        db_session, campaign_id=None, title="My Video", storage_path="/tmp/x.mp4"
    )
    from content_factory.db.models.clip import Clip

    clip = Clip(source_video_id=source_video.id, start_s=0.0, end_s=10.0, status=ClipStatus.SUGGESTED)
    db_session.add(clip)
    db_session.flush()

    class _LyingRenderer:
        def render(self, request):
            return ClipRenderResult(asset_url=str(tmp_media_dir / "clip.mp4"), duration_s=1.0, provider="lying")

    (tmp_media_dir).mkdir(parents=True, exist_ok=True)
    (tmp_media_dir / "clip.mp4").write_bytes(b"fake")

    video = clip_service.render_clip(db_session, clip=clip, source_video=source_video, clip_renderer=_LyingRenderer())

    assert video.qc_status == "failed"
    assert "differs from the requested" in video.qc_notes
