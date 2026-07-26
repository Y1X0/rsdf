from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from content_factory.db.base import Base
from content_factory.db.models.enums import ExperimentAxis, ExperimentStatus, ExperimentSubjectType
from content_factory.db.models.mixins import TimestampMixin


class Experiment(TimestampMixin, Base):
    """ARCHITECTURE.md §5 — one row per axis-scoped statistical run. Not a
    background scheduler job in Phase 2 (there is no worker infrastructure
    yet — modular monolith, see docs/PHASE1.md) — `POST /experimentation/run`
    computes and concludes an experiment synchronously in the same request.
    """

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(primary_key=True)
    axis: Mapped[ExperimentAxis] = mapped_column(Enum(ExperimentAxis, native_enum=False, length=16))
    niche_id: Mapped[int | None] = mapped_column(ForeignKey("niches.id"), nullable=True)
    status: Mapped[ExperimentStatus] = mapped_column(
        Enum(ExperimentStatus, native_enum=False, length=16), default=ExperimentStatus.RUNNING
    )
    min_sample_size: Mapped[int] = mapped_column(Integer)
    significance_threshold: Mapped[float] = mapped_column(Float)
    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True))
    concluded_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Experiment(axis={self.axis}, status={self.status})"


class ExperimentResult(TimestampMixin, Base):
    """One row per candidate subject (a hook, a niche, a length bucket, a
    posting-time bucket) considered by an Experiment. `applied_at`/
    `applied_by` are null until a human explicitly applies a *winning*
    recommendation via a separate action — `run_experiment` itself never
    sets them (ARCHITECTURE.md §7.2/§16's "recommend-only" constraint,
    structurally enforced rather than a comment).
    """

    __tablename__ = "experiment_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), index=True)

    subject_type: Mapped[ExperimentSubjectType] = mapped_column(
        Enum(ExperimentSubjectType, native_enum=False, length=20)
    )
    subject_key: Mapped[str] = mapped_column(String(200))
    sample_size: Mapped[int] = mapped_column(Integer)
    avg_viral_score: Mapped[float] = mapped_column(Float)
    # Indexed (Production Hardening Sprint H5): `GET /experimentation/
    # recommendations` filters on this by default (winners_only=True).
    is_winner: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    applied_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    computed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover
        return f"ExperimentResult(subject_key={self.subject_key!r}, is_winner={self.is_winner})"
