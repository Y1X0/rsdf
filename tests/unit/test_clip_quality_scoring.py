from content_factory.db.models.clip import Clip
from content_factory.db.models.clip_quality import ClipQualityScore
from content_factory.db.models.enums import ClipStatus, SourceVideoOrigin
from content_factory.db.models.source_video import SourceVideo
from content_factory.db.models.video import Video
from content_factory.services import clip_quality_scoring
from content_factory.transcription.base import TranscriptWord


def test_score_caption_coverage_is_100_when_words_span_the_whole_range():
    words = [
        TranscriptWord(start_s=0.0, end_s=2.0, word="hello"),
        TranscriptWord(start_s=2.0, end_s=4.0, word="world"),
    ]
    score = clip_quality_scoring.score_caption_coverage(words, start_s=0.0, end_s=4.0)
    assert score == 100.0


def test_score_caption_coverage_reflects_partial_coverage():
    words = [TranscriptWord(start_s=0.0, end_s=1.0, word="hi")]
    score = clip_quality_scoring.score_caption_coverage(words, start_s=0.0, end_s=4.0)
    assert score == 25.0


def test_score_caption_coverage_is_zero_with_no_overlapping_words():
    words = [TranscriptWord(start_s=10.0, end_s=11.0, word="later")]
    score = clip_quality_scoring.score_caption_coverage(words, start_s=0.0, end_s=4.0)
    assert score == 0.0


def test_score_scene_alignment_is_100_when_boundaries_sit_exactly_on_real_cuts():
    score = clip_quality_scoring.score_scene_alignment([2.0, 9.5], start_s=2.0, end_s=9.5)
    assert score == 100.0


def test_score_scene_alignment_is_zero_when_boundaries_are_far_from_any_cut():
    score = clip_quality_scoring.score_scene_alignment([50.0], start_s=2.0, end_s=9.5, tolerance_s=2.0)
    assert score == 0.0


def test_score_scene_alignment_is_none_when_no_scene_data_is_available():
    """A missing signal must be an honest 'unknown', never a fabricated 0
    that would read as 'badly misaligned' when it's really just
    unmeasured (e.g. ffmpeg unavailable, or the source file is gone)."""
    score = clip_quality_scoring.score_scene_alignment([], start_s=2.0, end_s=9.5)
    assert score is None


def _make_clip_and_video(db_session, *, hook_strength_score=80.0) -> tuple[Clip, Video]:
    source_video = SourceVideo(
        title="Test", storage_path="/tmp/does-not-need-to-exist.mp4", source=SourceVideoOrigin.UPLOAD
    )
    db_session.add(source_video)
    db_session.flush()
    clip = Clip(
        source_video_id=source_video.id, start_s=2.0, end_s=9.0,
        hook_text="hook", hook_strength_score=hook_strength_score, status=ClipStatus.SUGGESTED,
    )
    db_session.add(clip)
    db_session.flush()
    video = Video(clip_id=clip.id)
    db_session.add(video)
    db_session.flush()
    return clip, video


def test_score_clip_video_copies_hook_strength_from_the_clip(db_session):
    clip, video = _make_clip_and_video(db_session, hook_strength_score=73.5)
    words = [TranscriptWord(start_s=2.0, end_s=9.0, word="w")]

    quality = clip_quality_scoring.score_clip_video(
        db_session, video=video, clip=clip, words=words, scene_changes=[2.0, 9.0],
        render_start_s=2.0, render_end_s=9.0,
    )

    assert quality.hook_strength_score == 73.5
    assert quality.video_id == video.id


def test_score_clip_video_leaves_unmeasured_dimensions_explicitly_null(db_session):
    """retention/cta/speech_clarity have no real signal behind them yet -
    they must stay null (not a fabricated placeholder like 0 or 50)."""
    clip, video = _make_clip_and_video(db_session)

    quality = clip_quality_scoring.score_clip_video(
        db_session, video=video, clip=clip, words=[], scene_changes=[],
        render_start_s=2.0, render_end_s=9.0,
    )

    assert quality.retention_prediction_score is None
    assert quality.cta_quality_score is None
    assert quality.speech_clarity_score is None


def test_score_clip_video_persists_a_real_row_queryable_by_video_id(db_session):
    clip, video = _make_clip_and_video(db_session)

    clip_quality_scoring.score_clip_video(
        db_session, video=video, clip=clip, words=[], scene_changes=[],
        render_start_s=2.0, render_end_s=9.0,
    )

    row = db_session.query(ClipQualityScore).filter(ClipQualityScore.video_id == video.id).one()
    assert row.model_version == "heuristic-v1"


def test_score_clip_video_scoring_a_second_time_updates_the_same_row_not_a_duplicate(db_session):
    """Regression test: re-scoring the same video (a future recompute/
    backfill, or any caller invoking this twice) must update the one
    existing row in place - never crash on the video_id unique constraint
    and never create a second row. This is the table meant to become the
    foundation for future ranking/analytics, so exactly-one-row-per-video
    must hold even under a second call, not just under normal single-call
    usage."""
    clip, video = _make_clip_and_video(db_session, hook_strength_score=50.0)

    clip_quality_scoring.score_clip_video(
        db_session, video=video, clip=clip, words=[], scene_changes=[],
        render_start_s=2.0, render_end_s=9.0,
    )
    assert db_session.query(ClipQualityScore).filter(ClipQualityScore.video_id == video.id).count() == 1

    # Simulate the clip's hook score having been recomputed/updated since
    # the first scoring pass.
    clip.hook_strength_score = 91.0
    clip_quality_scoring.score_clip_video(
        db_session, video=video, clip=clip, words=[], scene_changes=[],
        render_start_s=2.0, render_end_s=9.0,
    )

    rows = db_session.query(ClipQualityScore).filter(ClipQualityScore.video_id == video.id).all()
    assert len(rows) == 1
    assert rows[0].hook_strength_score == 91.0


def test_score_clip_video_never_clobbers_a_later_phases_real_signal_on_rescoring(db_session):
    """retention_prediction_score/cta_quality_score/speech_clarity_score
    stay None only until some future phase populates one with a real
    value (a plain UPDATE against this same row) - re-running
    score_clip_video afterward (e.g. to refresh hook_strength/caption/
    scene signals) must never silently wipe that real value back to
    None."""
    clip, video = _make_clip_and_video(db_session)

    clip_quality_scoring.score_clip_video(
        db_session, video=video, clip=clip, words=[], scene_changes=[],
        render_start_s=2.0, render_end_s=9.0,
    )
    row = db_session.query(ClipQualityScore).filter(ClipQualityScore.video_id == video.id).one()
    row.retention_prediction_score = 77.0  # a hypothetical future phase populating a real value
    db_session.flush()

    clip_quality_scoring.score_clip_video(
        db_session, video=video, clip=clip, words=[], scene_changes=[],
        render_start_s=2.0, render_end_s=9.0,
    )

    db_session.refresh(row)
    assert row.retention_prediction_score == 77.0
