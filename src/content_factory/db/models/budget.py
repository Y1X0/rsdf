from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from content_factory.db.base import Base
from content_factory.db.models.enums import BudgetScope
from content_factory.db.models.mixins import TimestampMixin


class BudgetCeiling(TimestampMixin, Base):
    """ARCHITECTURE.md §10c — a human-set monthly spend ceiling, system-wide
    or per-niche. Append-only by convention (a new ceiling is a new row,
    "latest wins" when read) except for `last_alert_threshold_pct`, which
    is intentionally mutable: it tracks which alert threshold has already
    fired for the *current* ceiling so `services/budget_governor.py` fires
    each of 50/80/95/100% exactly once per crossing instead of on every
    request that happens to check it.
    """

    __tablename__ = "budget_ceilings"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[BudgetScope] = mapped_column(Enum(BudgetScope, native_enum=False, length=16))
    niche_id: Mapped[int | None] = mapped_column(ForeignKey("niches.id"), nullable=True, index=True)
    monthly_limit_usd: Mapped[float] = mapped_column(Float)
    last_alert_threshold_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"BudgetCeiling(scope={self.scope}, niche_id={self.niche_id}, limit={self.monthly_limit_usd})"


class ProviderQuotaUsage(TimestampMixin, Base):
    """ARCHITECTURE.md §10b's "two distinct kinds of ceiling" — this is the
    count/quota-based one (e.g. YouTube's daily unit budget), independent
    of the dollar-denominated `cost_ledger`/`BudgetCeiling` pair. One row
    per (provider, period); upserted via services/db_safety.get_or_create
    the same way niches/hooks are in Phase 1, backed by the unique
    constraint below rather than a check-then-act race.
    """

    __tablename__ = "provider_quota_usage"
    __table_args__ = (
        UniqueConstraint("provider", "quota_period", "period_start", name="uq_provider_quota_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    quota_period: Mapped[str] = mapped_column(String(20), default="daily")
    period_start: Mapped[DateTime] = mapped_column(DateTime(timezone=True))
    units_used: Mapped[int] = mapped_column(Integer, default=0)
    units_limit: Mapped[int] = mapped_column(Integer)

    def __repr__(self) -> str:  # pragma: no cover
        return f"ProviderQuotaUsage(provider={self.provider!r}, used={self.units_used}/{self.units_limit})"
