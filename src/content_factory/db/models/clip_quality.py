from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from content_factory.db.base import Base
from content_factory.db.models.mixins import TimestampMixin


class ClipQualityScore(TimestampMixin, Base):
    """Post-render quality scoring for Clip Factory videos.

    Deliberately a separate table from QualityScore
    (services/quality_scoring.py), which is shaped entirely for the
    Script pipeline's AI-generated text (originality vs. prior scripts,
    policy-risk keyword scan) and has no room for a Clip's own real
    signals - real footage isn't "original" or "not original" the way
    generated text is.

    Only hook_strength_score/caption_coverage_score/scene_alignment_score
    are computed from real data today (services/clip_quality_scoring.py,
    each grounded in something this pipeline already produces: Clip's own
    pre-computed hook score, real per-word transcript timestamps, real
    ffmpeg scene-cut detection). retention_prediction_score/
    cta_quality_score/speech_clarity_score stay nullable placeholders for
    a later phase - same "null = not yet available" convention
    QualityScore's own retention_prediction_score/
    monetization_probability_score already established - never a
    fabricated placeholder number.
    """

    __tablename__ = "clip_quality_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), unique=True, index=True)

    hook_strength_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    caption_coverage_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    scene_alignment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    retention_prediction_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cta_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    speech_clarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    model_version: Mapped[str] = mapped_column(String(50), default="heuristic-v1")
    computed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover
        return f"ClipQualityScore(video_id={self.video_id}, hook_strength={self.hook_strength_score})"
