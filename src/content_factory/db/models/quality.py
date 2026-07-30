from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from content_factory.db.base import Base
from content_factory.db.models.mixins import TimestampMixin


class QualityScore(TimestampMixin, Base):
    """ARCHITECTURE.md §6, minimal heuristic version approved for Phase 1.

    Only originality and policy-risk are computed from real (if simple)
    logic in Phase 1 — see services/quality_scoring.py. retention_prediction
    and monetization_probability columns exist now so Phase 2 can populate
    them with a learned model via an additive UPDATE, not a migration; they
    are explicitly nullable and the API/reviewer UI must treat null as
    "not yet available" rather than "zero".
    """

    __tablename__ = "quality_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), unique=True, index=True)

    originality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    retention_prediction_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    policy_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    monetization_probability_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    model_version: Mapped[str] = mapped_column(String(50), default="heuristic-v1")
    computed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover
        return f"QualityScore(video_id={self.video_id}, originality={self.originality_score})"
