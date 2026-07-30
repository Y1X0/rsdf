"""Experimentation Engine (ARCHITECTURE.md §5) — Phase 2 M6.

Deliberately a Phase 2 simplification, matching this codebase's established
heuristic style elsewhere (quality_scoring's Jaccard overlap,
campaign_scoring's weighted composite): `significance_threshold` here is a
required margin over the axis's own baseline (the mean of all *other*
eligible subjects), not a p-value from a real statistical test —
ARCHITECTURE.md §5 itself flags that real significance testing needs more
data density than Phase 1/2 volume can supply.

**Recommend-only, always:** `run_experiment` only ever writes
`experiment_results` rows — it never touches `learning_patterns
.confidence_tier` or `niches.allocation_weight`. Only `apply_recommendation`
(called from a separate, explicit endpoint, never automatically) does
that, and only for the one winning result an operator picks. This is what
makes ARCHITECTURE.md §7.2/§16's "recommend-only, human applies" a
structural guarantee rather than a comment.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from content_factory.db.models.analytics import ViralScoreRecord
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.content import ContentIdea, Script
from content_factory.db.models.enums import (
    ExperimentAxis,
    ExperimentStatus,
    ExperimentSubjectType,
    PatternConfidenceTier,
    PatternType,
)
from content_factory.db.models.experiment import Experiment, ExperimentResult
from content_factory.db.models.hook import LearningPattern
from content_factory.db.models.niche import Niche
from content_factory.db.models.publication import Publication
from content_factory.db.models.video import Video
from content_factory.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_MIN_SAMPLE_SIZE = 5
DEFAULT_SIGNIFICANCE_THRESHOLD = 0.10  # candidate must beat baseline by >=10%

# Niche-axis "epsilon-greedy" split (§5): the winning niche gets the large
# majority of the allocation weight, not all of it — a fixed remainder
# stays available for exploring the rest of the portfolio.
NICHE_WINNER_ALLOCATION_WEIGHT = 0.8

_LENGTH_BUCKET_CEILINGS = ((15, "0-15s"), (30, "15-30s"), (60, "30-60s"))
_LENGTH_BUCKET_OVERFLOW = "60s+"


def _length_bucket(duration_s: float) -> str:
    for ceiling, label in _LENGTH_BUCKET_CEILINGS:
        if duration_s <= ceiling:
            return label
    return _LENGTH_BUCKET_OVERFLOW


class UnknownAxis(Exception):
    pass


class RecommendationNotWinner(Exception):
    pass


@dataclass
class _Sample:
    subject_key: str
    scores: list[float] = field(default_factory=list)

    @property
    def sample_size(self) -> int:
        return len(self.scores)

    @property
    def avg_score(self) -> float:
        return sum(self.scores) / len(self.scores) if self.scores else 0.0


def _collect_hook_samples(db: Session, *, niche_id: int | None) -> dict[str, _Sample]:
    query = (
        db.query(Script.hook_text, ViralScoreRecord.score)
        .join(Video, ViralScoreRecord.video_id == Video.id)
        .join(Script, Video.script_id == Script.id)
    )
    if niche_id is not None:
        query = (
            query.join(ContentIdea, Script.idea_id == ContentIdea.id)
            .join(Campaign, ContentIdea.campaign_id == Campaign.id)
            .filter(Campaign.niche_id == niche_id)
        )
    samples: dict[str, _Sample] = {}
    for hook_text, score in query.all():
        samples.setdefault(hook_text, _Sample(subject_key=hook_text)).scores.append(float(score))
    return samples


def _collect_niche_samples(db: Session, *, niche_id: int | None) -> dict[str, _Sample]:
    query = (
        db.query(Campaign.niche_id, ViralScoreRecord.score)
        .join(Video, ViralScoreRecord.video_id == Video.id)
        .join(Script, Video.script_id == Script.id)
        .join(ContentIdea, Script.idea_id == ContentIdea.id)
        .join(Campaign, ContentIdea.campaign_id == Campaign.id)
        .filter(Campaign.niche_id.isnot(None))
    )
    samples: dict[str, _Sample] = {}
    for row_niche_id, score in query.all():
        key = str(row_niche_id)
        samples.setdefault(key, _Sample(subject_key=key)).scores.append(float(score))
    return samples


def _collect_length_samples(db: Session, *, niche_id: int | None) -> dict[str, _Sample]:
    query = (
        db.query(Publication.platform, Video.duration_s, ViralScoreRecord.score)
        .join(Video, Publication.video_id == Video.id)
        .join(ViralScoreRecord, ViralScoreRecord.video_id == Video.id)
        .filter(Video.duration_s.isnot(None))
    )
    if niche_id is not None:
        query = (
            query.join(Script, Video.script_id == Script.id)
            .join(ContentIdea, Script.idea_id == ContentIdea.id)
            .join(Campaign, ContentIdea.campaign_id == Campaign.id)
            .filter(Campaign.niche_id == niche_id)
        )
    samples: dict[str, _Sample] = {}
    for platform, duration_s, score in query.all():
        key = f"{platform.value}:{_length_bucket(duration_s)}"
        samples.setdefault(key, _Sample(subject_key=key)).scores.append(float(score))
    return samples


def _collect_posting_time_samples(db: Session, *, niche_id: int | None) -> dict[str, _Sample]:
    query = (
        db.query(Publication.platform, Publication.published_at, ViralScoreRecord.score)
        .join(Video, Publication.video_id == Video.id)
        .join(ViralScoreRecord, ViralScoreRecord.video_id == Video.id)
        .filter(Publication.published_at.isnot(None))
    )
    if niche_id is not None:
        query = (
            query.join(Script, Video.script_id == Script.id)
            .join(ContentIdea, Script.idea_id == ContentIdea.id)
            .join(Campaign, ContentIdea.campaign_id == Campaign.id)
            .filter(Campaign.niche_id == niche_id)
        )
    samples: dict[str, _Sample] = {}
    for platform, published_at, score in query.all():
        key = f"{platform.value}:{published_at.strftime('%a').lower()}:{published_at.hour:02d}h"
        samples.setdefault(key, _Sample(subject_key=key)).scores.append(float(score))
    return samples


_AXIS_SUBJECT_TYPE = {
    ExperimentAxis.HOOK: ExperimentSubjectType.HOOK,
    ExperimentAxis.NICHE: ExperimentSubjectType.NICHE,
    ExperimentAxis.LENGTH: ExperimentSubjectType.LENGTH_BUCKET,
    ExperimentAxis.POSTING_TIME: ExperimentSubjectType.POSTING_TIME_BUCKET,
}

_AXIS_COLLECTORS = {
    ExperimentAxis.HOOK: _collect_hook_samples,
    ExperimentAxis.NICHE: _collect_niche_samples,
    ExperimentAxis.LENGTH: _collect_length_samples,
    ExperimentAxis.POSTING_TIME: _collect_posting_time_samples,
}


def run_experiment(
    db: Session,
    *,
    axis: ExperimentAxis,
    niche_id: int | None = None,
    min_sample_size: int = DEFAULT_MIN_SAMPLE_SIZE,
    significance_threshold: float = DEFAULT_SIGNIFICANCE_THRESHOLD,
) -> Experiment:
    if axis not in _AXIS_COLLECTORS:
        raise UnknownAxis(f"Unknown experimentation axis: {axis!r}")  # pragma: no cover - enum is exhaustive

    now = datetime.now(UTC)
    experiment = Experiment(
        axis=axis,
        niche_id=niche_id,
        status=ExperimentStatus.RUNNING,
        min_sample_size=min_sample_size,
        significance_threshold=significance_threshold,
        started_at=now,
    )
    db.add(experiment)
    db.flush()

    samples = _AXIS_COLLECTORS[axis](db, niche_id=niche_id)
    subject_type = _AXIS_SUBJECT_TYPE[axis]

    eligible = {key: s for key, s in samples.items() if s.sample_size >= min_sample_size}
    winner_key = None
    for key, sample in eligible.items():
        others = [s.avg_score for k, s in eligible.items() if k != key]
        if not others:
            continue  # can't establish a baseline against a field of one
        baseline = sum(others) / len(others)
        if sample.avg_score > baseline * (1 + significance_threshold):
            if winner_key is None or sample.avg_score > eligible[winner_key].avg_score:
                winner_key = key

    for key, sample in samples.items():
        db.add(
            ExperimentResult(
                experiment_id=experiment.id,
                subject_type=subject_type,
                subject_key=key,
                sample_size=sample.sample_size,
                avg_viral_score=sample.avg_score,
                is_winner=(key == winner_key),
                computed_at=now,
            )
        )

    experiment.status = ExperimentStatus.CONCLUDED if winner_key else ExperimentStatus.INCONCLUSIVE
    experiment.concluded_at = now
    db.flush()

    logger.info(
        "experiment_run_completed",
        axis=axis.value,
        niche_id=niche_id,
        subject_count=len(samples),
        winner=winner_key,
        status=experiment.status.value,
    )
    return experiment


def apply_recommendation(db: Session, *, result: ExperimentResult, applied_by: str) -> ExperimentResult:
    """The one place a recommendation's effect (if any) actually lands —
    always a separate, explicit human action. Idempotent: re-applying an
    already-applied result is a no-op, not an error."""
    if not result.is_winner:
        raise RecommendationNotWinner(f"ExperimentResult {result.id} is not a winning recommendation.")
    if result.applied_at is not None:
        return result

    experiment = db.get(Experiment, result.experiment_id)

    if experiment.axis == ExperimentAxis.NICHE:
        niche = db.get(Niche, int(result.subject_key))
        if niche is not None:
            niche.allocation_weight = NICHE_WINNER_ALLOCATION_WEIGHT

    elif experiment.axis == ExperimentAxis.HOOK:
        description = f"winning_hook:{result.subject_key}"
        pattern = db.query(LearningPattern).filter(LearningPattern.description == description).one_or_none()
        if pattern is None:
            pattern = LearningPattern(
                niche_id=experiment.niche_id,
                pattern_type=PatternType.HOOK,
                description=description,
                confidence_tier=PatternConfidenceTier.CONFIRMED,
                confidence_score=result.avg_viral_score,
            )
            db.add(pattern)
        else:
            pattern.confidence_tier = PatternConfidenceTier.CONFIRMED
            pattern.confidence_score = result.avg_viral_score

    # LENGTH/POSTING_TIME axes have no existing downstream column to write
    # yet (ARCHITECTURE.md §5's "feeds Script Agent's target_duration_s
    # guidance" needs a guidance store this phase doesn't add) — applying
    # still records human endorsement via applied_at/applied_by below,
    # a documented gap rather than a silent one.

    result.applied_at = datetime.now(UTC)
    result.applied_by = applied_by
    db.flush()

    logger.info("experiment_recommendation_applied", result_id=result.id, applied_by=applied_by)
    return result
