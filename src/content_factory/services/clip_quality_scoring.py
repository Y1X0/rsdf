"""Post-render Clip Factory quality scoring.

Deliberately separate from services/quality_scoring.py's QualityScore,
which is shaped entirely for the Script pipeline's AI-generated text
(originality vs. prior scripts, policy-risk keyword scan) - none of that
applies to real footage. Every dimension computed here is grounded in
data this pipeline already produces for real today:

- hook_strength_score: copied straight from Clip.hook_strength_score
  (already computed at clip-selection time by services/hook_scoring.py).
- caption_coverage_score: what fraction of the rendered clip's own
  duration is actually covered by real, timestamped transcript words -
  not a guess, computed directly from the same per-word timestamps the
  renderer itself uses for captions.
- scene_alignment_score: how close the clip's actual render boundaries
  land to a real detected visual cut (video_clipping/scene_detection.py),
  using the same SCENE_SNAP_TOLERANCE_S already used to decide whether to
  snap a boundary onto one.

retention_prediction_score/cta_quality_score/speech_clarity_score stay
explicit nulls - there is no real signal for any of them yet (no
engagement-based retention model, no CTA concept exists for clips, no
transcription-confidence data is extracted from the transcription
provider) - same "null = not yet available" convention QualityScore's own
retention_prediction_score/monetization_probability_score already
established. Never fabricate a placeholder number here.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from content_factory.db.models.clip import Clip
from content_factory.db.models.clip_quality import ClipQualityScore
from content_factory.db.models.video import Video
from content_factory.logging_config import get_logger
from content_factory.transcription.base import TranscriptWord
from content_factory.video_clipping.scene_detection import SCENE_SNAP_TOLERANCE_S

logger = get_logger(__name__)


def score_caption_coverage(words: list[TranscriptWord], *, start_s: float, end_s: float) -> float | None:
    """0-100: what fraction of [start_s, end_s]'s own duration is actually
    covered by a real, timestamped transcript word - not just whether
    captions exist, but how much dead air/silence they leave uncovered."""
    duration = end_s - start_s
    if duration <= 0:
        return None
    covered_s = sum(
        max(0.0, min(w.end_s, end_s) - max(w.start_s, start_s))
        for w in words
        if w.end_s > start_s and w.start_s < end_s
    )
    return round(min(100.0, 100 * covered_s / duration), 2)


def score_scene_alignment(
    scene_changes: list[float], *, start_s: float, end_s: float, tolerance_s: float = SCENE_SNAP_TOLERANCE_S
) -> float | None:
    """0-100: how close the clip's actual start/end land to a real
    detected visual cut - 100 means both boundaries sit exactly on a real
    cut, decaying to 0 at tolerance_s away or further (same tolerance
    already used to decide whether to snap a boundary onto one). None
    (not 0) when no scene-change data is available at all - an unknown,
    not a bad score."""
    if not scene_changes:
        return None
    start_dist = min(abs(t - start_s) for t in scene_changes)
    end_dist = min(abs(t - end_s) for t in scene_changes)
    avg_dist = (start_dist + end_dist) / 2
    return round(max(0.0, 100 * (1 - avg_dist / tolerance_s)), 2)


def score_clip_video(
    db: Session,
    *,
    video: Video,
    clip: Clip,
    words: list[TranscriptWord],
    scene_changes: list[float],
    render_start_s: float,
    render_end_s: float,
) -> ClipQualityScore:
    quality = ClipQualityScore(
        video_id=video.id,
        hook_strength_score=clip.hook_strength_score,
        caption_coverage_score=score_caption_coverage(words, start_s=render_start_s, end_s=render_end_s),
        scene_alignment_score=score_scene_alignment(scene_changes, start_s=render_start_s, end_s=render_end_s),
        retention_prediction_score=None,
        cta_quality_score=None,
        speech_clarity_score=None,
        model_version="heuristic-v1",
        computed_at=datetime.now(UTC),
    )
    db.add(quality)
    db.flush()

    logger.info(
        "clip_quality_score_computed",
        video_id=video.id,
        clip_id=clip.id,
        hook_strength_score=quality.hook_strength_score,
        caption_coverage_score=quality.caption_coverage_score,
        scene_alignment_score=quality.scene_alignment_score,
    )
    return quality
