from sqlalchemy import JSON, Boolean, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from content_factory.db.base import Base
from content_factory.db.models.enums import CampaignStatus, ProcessingStatus
from content_factory.db.models.mixins import TimestampMixin


class Campaign(TimestampMixin, Base):
    """A Whop Content Rewards campaign, entered manually in Phase 1.

    ARCHITECTURE.md §0 flags that Whop has no confirmed public API for
    campaign discovery — so `whop_campaign_id` is an optional external
    reference (fill it in when known) rather than a required foreign key
    into anything we control. `rules_json` holds the freeform rules text the
    human operator transcribes from the Whop dashboard; the review workflow
    (services/review_service.py) surfaces it as a checklist per
    ARCHITECTURE.md §7.1.
    """

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Natural external key when known — unique but nullable, since manually
    # entered campaigns may not have one. Used as part of the idempotency
    # fingerprint in services/idempotency.py.
    whop_campaign_id: Mapped[str | None] = mapped_column(
        String(200), unique=True, nullable=True, index=True
    )

    brand_name: Mapped[str] = mapped_column(String(200))
    niche_id: Mapped[int | None] = mapped_column(ForeignKey("niches.id"), nullable=True)

    payout_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cpm_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_remaining_est: Mapped[float | None] = mapped_column(Float, nullable=True)

    rules_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ai_content_allowed: Mapped[bool] = mapped_column(Boolean, default=True)

    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, native_enum=False, length=32),
        default=CampaignStatus.DISCOVERED,
    )

    niche: Mapped["Niche"] = relationship()  # noqa: F821
    scores: Mapped[list["CampaignScore"]] = relationship(
        back_populates="campaign", order_by="CampaignScore.created_at.desc()"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"Campaign(id={self.id}, brand={self.brand_name!r})"


class CampaignScore(TimestampMixin, Base):
    """Campaign Intelligence scoring output (ARCHITECTURE.md §3).

    One row per scoring run (append-only, not upserted) so the score's
    evolution over time is visible — competition/difficulty legitimately
    change as more creators join a campaign. `status` + timestamps follow
    the same pending/in_progress/completed/failed pattern as every other
    agent-adjacent table, even though Phase 1 computes it synchronously.
    """

    __tablename__ = "campaign_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)

    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, native_enum=False, length=32),
        default=ProcessingStatus.PENDING,
    )

    expected_roi_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_roi_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_roi_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    difficulty_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    competition_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    niche_fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(50), nullable=True)
    breakdown_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    campaign: Mapped["Campaign"] = relationship(back_populates="scores")

    def __repr__(self) -> str:  # pragma: no cover
        return f"CampaignScore(campaign_id={self.campaign_id}, composite={self.composite_score})"
