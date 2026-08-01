import json

import pytest

from content_factory.agents.clip_selection_agent import ClipSelectionAgent
from content_factory.db.models.enums import ClipStatus
from content_factory.db.models.hook import HookLibrary
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


def test_select_clips_snaps_boundaries_onto_a_nearby_real_scene_change(db_session):
    """A clip boundary suggested by the LLM close to (but not exactly on)
    a real detected scene cut should be pulled onto that cut, so the
    rendered clip doesn't start/end mid-shot."""
    canned = [{"start_s": 2.3, "end_s": 9.4, "hook_text": "hook", "predicted_score": 0.8, "reason": "n/a"}]
    llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))
    agent = ClipSelectionAgent(llm)
    source_video = _source_video(db_session)

    clips = agent.select_clips(
        db_session, source_video=source_video, segments=_segments(), max_clips=5, scene_changes=[2.0, 9.5]
    )

    assert len(clips) == 1
    assert clips[0].start_s == 2.0
    assert clips[0].end_s == 9.5


def test_select_clips_leaves_boundaries_untouched_when_no_scene_change_is_close(db_session):
    canned = [{"start_s": 2.0, "end_s": 8.0, "hook_text": "hook", "predicted_score": 0.8, "reason": "n/a"}]
    llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))
    agent = ClipSelectionAgent(llm)
    source_video = _source_video(db_session)

    clips = agent.select_clips(
        db_session, source_video=source_video, segments=_segments(), max_clips=5, scene_changes=[19.9]
    )

    assert clips[0].start_s == 2.0
    assert clips[0].end_s == 8.0


def test_select_clips_stores_a_recognized_hook_framework_and_computed_strength_score(db_session):
    canned = [
        {
            "start_s": 2.0, "end_s": 8.0,
            "hook_framework": "curiosity_gap",
            "hook_text": "The one mistake that's costing you followers",
            "predicted_score": 0.9, "reason": "n/a",
        }
    ]
    llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))
    agent = ClipSelectionAgent(llm)
    source_video = _source_video(db_session)

    clips = agent.select_clips(db_session, source_video=source_video, segments=_segments(), max_clips=5)

    assert len(clips) == 1
    assert clips[0].hook_framework == "curiosity_gap"
    assert clips[0].hook_strength_score is not None
    assert 0 <= clips[0].hook_strength_score <= 100


def test_select_clips_discards_an_unrecognized_hook_framework(db_session):
    canned = [
        {
            "start_s": 2.0, "end_s": 8.0,
            "hook_framework": "invented_by_the_llm",
            "hook_text": "hook",
            "predicted_score": 0.5, "reason": "n/a",
        }
    ]
    llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))
    agent = ClipSelectionAgent(llm)
    source_video = _source_video(db_session)

    clips = agent.select_clips(db_session, source_video=source_video, segments=_segments(), max_clips=5)

    assert clips[0].hook_framework is None


def test_prompt_includes_the_full_hook_framework_taxonomy():
    from content_factory.services.hook_scoring import HOOK_FRAMEWORKS

    prompt = ClipSelectionAgent._build_prompt(segments=_segments(), max_clips=5, retrieved_hooks=[])

    for key in HOOK_FRAMEWORKS:
        assert key in prompt


def test_prompt_includes_real_retrieved_hooks_not_a_static_list():
    retrieved_hooks = [
        HookLibrary(hook_text="The one mistake that's costing you followers", hook_type="curiosity_gap", best_viral_score=0.95),
        HookLibrary(hook_text="Stop scrolling - this changes everything", hook_type="pattern_interrupt", best_viral_score=0.8),
    ]

    prompt = ClipSelectionAgent._build_prompt(segments=_segments(), max_clips=5, retrieved_hooks=retrieved_hooks)

    assert "Highest-performing hooks previously observed for this niche:" in prompt
    assert "The one mistake that's costing you followers" in prompt
    assert "Stop scrolling - this changes everything" in prompt


def test_prompt_falls_back_to_no_prior_hook_data_yet_when_none_retrieved():
    prompt = ClipSelectionAgent._build_prompt(segments=_segments(), max_clips=5, retrieved_hooks=[])

    assert "(no prior hook data yet)" in prompt


def test_select_clips_produces_identical_clips_whether_or_not_hooks_were_retrieved(db_session):
    """Hook retrieval is additive prompt context only - it must never
    change how a valid LLM response is parsed into Clip rows."""
    canned = [{"start_s": 2.0, "end_s": 8.0, "hook_text": "hook", "predicted_score": 0.9, "reason": "n/a"}]
    llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))
    agent = ClipSelectionAgent(llm)

    source_video_a = _source_video(db_session)
    clips_without_hooks = agent.select_clips(
        db_session, source_video=source_video_a, segments=_segments(), max_clips=5, retrieved_hooks=[]
    )

    source_video_b = _source_video(db_session)
    retrieved_hooks = [HookLibrary(hook_text="prior hook", hook_type="bold_claim", best_viral_score=0.7)]
    clips_with_hooks = agent.select_clips(
        db_session, source_video=source_video_b, segments=_segments(), max_clips=5, retrieved_hooks=retrieved_hooks
    )

    assert len(clips_without_hooks) == len(clips_with_hooks) == 1
    assert clips_without_hooks[0].start_s == clips_with_hooks[0].start_s
    assert clips_without_hooks[0].end_s == clips_with_hooks[0].end_s
    assert clips_without_hooks[0].hook_text == clips_with_hooks[0].hook_text


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
