from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from content_factory.db.base import Base
from content_factory.db.models.mixins import TimestampMixin


class MetricsSnapshot(TimestampMixin, Base):
    """ARCHITECTURE.md §12 — raw performance data, manually entered in Phase 1
    (no platform Analytics API integration yet; publishing itself is out of
    Phase 1 scope). One row per observation point in time, append-only, so
    the same video can be checked at 1h/24h/7d etc. without overwriting
    earlier readings.
    """

    __tablename__ = "metrics_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)

    captured_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True))
    views: Mapped[int] = mapped_column(Integer, default=0)
    avg_watch_time_s: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    completion_rate: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    rewatch_rate: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(20), default="manual")

    def __repr__(self) -> str:  # pragma: no cover
        return f"MetricsSnapshot(video_id={self.video_id}, views={self.views})"


class ViralScoreRecord(TimestampMixin, Base):
    """ARCHITECTURE.md §2.5 — the computed Viral Score for a given
    MetricsSnapshot, using the specified weighting:

        0.40*watch_time + 0.25*completion_rate + 0.15*shares
        + 0.10*comments + 0.10*likes

    Phase 1 simplification (documented, not hidden): proper cross-video
    normalization needs a trailing-window baseline per niche/account that
    doesn't exist yet with only a handful of published videos. See
    services/analytics_service.py for the exact interim normalization used
    and the TODO marking where per-niche z-score baselines plug in once
    enough history exists.
    """

    __tablename__ = "viral_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    metrics_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("metrics_snapshots.id"), unique=True, index=True
    )
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)

    score: Mapped[float] = mapped_column(Numeric(6, 4))
    breakdown_json: Mapped[dict] = mapped_column(JSON)
    recommendation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    """'duplicate' | 'iterate' | 'retire' — ARCHITECTURE.md §2.5."""

    def __repr__(self) -> str:  # pragma: no cover
        return f"ViralScoreRecord(video_id={self.video_id}, score={self.score})"


class CostLedger(TimestampMixin, Base):
    """ARCHITECTURE.md §10a — the Cost Control Layer's spend ledger.

    Populated two ways: automatically whenever an AgentRun completes with a
    cost (see services/analytics_service.record_agent_run_cost, called from
    agents/base.py's recorder), and manually via the cost-entry API for
    non-AI costs (human review time, misc tooling) per ARCHITECTURE.md §22's
    "manual cost tracking" MVP scope.
    """

    __tablename__ = "cost_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"), nullable=True, index=True)
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id"), nullable=True, index=True
    )

    category: Mapped[str] = mapped_column(String(20))
    """'llm' | 'tts' | 'render' | 'human_review' | 'other'."""
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 4))
    # Indexed (Production Hardening Sprint H5): filtered with `>=` on every
    # `enforce_budget` call (services/budget_governor.py::_compute_spend),
    # i.e. every cost-incurring endpoint request — a hot path.
    recorded_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"CostLedger(video_id={self.video_id}, category={self.category}, cost={self.cost_usd})"


class RevenueSnapshot(TimestampMixin, Base):
    """ARCHITECTURE.md §9/§12 — realized (or pending) Whop payout for a video."""

    __tablename__ = "revenue_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)

    captured_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True))
    raw_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payout_realized: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    payout_pending: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")

    def __repr__(self) -> str:  # pragma: no cover
        return f"RevenueSnapshot(video_id={self.video_id}, payout={self.payout_realized})"
