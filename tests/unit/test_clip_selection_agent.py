import json

import pytest

from content_factory.agents.clip_selection_agent import ClipSelectionAgent
from content_factory.db.models.enums import ClipStatus
from content_factory.db.models.source_video import SourceVideo
from content_factory.llm.providers.fake_provider import FakeLLMClient
from content_factory.transcription.base import TranscriptSegment


def _source_video(db_session) -> SourceVideo:
    sv = SourceVideo(title="Test Video", storage_path="/tmp/does-not-need-to-exist.mp4")
    db_session.add(sv)
    db_session.flush()
    return sv


def _segments() -> list[TranscriptSegment]:
    return [
        TranscriptSegment(start_s=0.0, end_s=10.0, text="Intro segment."),
        TranscriptSegment(start_s=10.0, end_s=20.0, text="The big reveal moment."),
    ]


def test_select_clips_creates_clip_rows_from_valid_candidates(db_session):
    canned = [
        {"start_s": 2.0, "end_s": 8.0, "hook_text": "You won't believe this", "predicted_score": 0.9, "reason": "strong hook"},
        {"start_s": 11.0, "end_s": 18.0, "hook_text": "The reveal", "predicted_score": 0.7, "reason": "high engagement"},
    ]
    llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))
    agent = ClipSelectionAgent(llm)
    source_video = _source_video(db_session)

    clips = agent.select_clips(db_session, source_video=source_video, segments=_segments(), max_clips=5)

    assert len(clips) == 2
    assert clips[0].start_s == 2.0 and clips[0].end_s == 8.0
    assert clips[0].hook_text == "You won't believe this"
    assert all(c.status == ClipStatus.SUGGESTED for c in clips)
    assert all(c.source_video_id == source_video.id for c in clips)


def test_select_clips_rejects_candidates_outside_transcript_bounds(db_session):
    """A hallucinated timestamp past the real transcript's own range must
    never become a clip request an ffmpeg renderer would later choke on."""
    canned = [
        {"start_s": 5.0, "end_s": 500.0, "hook_text": "hallucinated", "predicted_score": 0.5, "reason": "n/a"},
        {"start_s": -3.0, "end_s": 5.0, "hook_text": "negative start", "predicted_score": 0.5, "reason": "n/a"},
        {"start_s": 5.0, "end_s": 5.0, "hook_text": "zero length", "predicted_score": 0.5, "reason": "n/a"},
        {"start_s": 1.0, "end_s": 9.0, "hook_text": "valid", "predicted_score": 0.5, "reason": "n/a"},
    ]
    llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))
    agent = ClipSelectionAgent(llm)
    source_video = _source_video(db_session)

    clips = agent.select_clips(db_session, source_video=source_video, segments=_segments(), max_clips=5)

    assert len(clips) == 1
    assert clips[0].hook_text == "valid"


def test_select_clips_returns_empty_list_on_unparseable_response(db_session):
    llm = FakeLLMClient(response_builder=lambda system, prompt: "not json")
    agent = ClipSelectionAgent(llm)
    source_video = _source_video(db_session)

    clips = agent.select_clips(db_session, source_video=source_video, segments=_segments(), max_clips=5)
    assert clips == []


def test_select_clips_marks_agent_run_failed_and_reraises_on_error(db_session):
    def _boom(system, prompt):
        raise RuntimeError("provider unavailable")

    llm = FakeLLMClient(response_builder=_boom)
    agent = ClipSelectionAgent(llm)
    source_video = _source_video(db_session)

    with pytest.raises(RuntimeError):
        agent.select_clips(db_session, source_video=source_video, segments=_segments(), max_clips=5)

    from content_factory.db.models.agent_run import AgentRun

    run = db_session.query(AgentRun).filter(AgentRun.agent_name == "clip_selection_agent").one()
    assert run.status.value == "failed"
    assert run.error_message == "provider unavailable"
