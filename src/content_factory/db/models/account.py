from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from content_factory.db.base import Base
from content_factory.db.models.enums import AccountHealthTier, AccountPlatform, AccountStatus, AccountWarmupStatus
from content_factory.db.models.mixins import TimestampMixin


class OwnedAccount(TimestampMixin, Base):
    """ARCHITECTURE.md §8 — one row per platform account the operator owns.

    `encrypted_oauth_token` is nullable: a real OAuth connection is not
    required to register and track an account (health scoring, warmup, and
    niche assignment are all useful before any live platform integration
    exists) — see services/token_encryption.py for how it's populated.
    Never serialized as-is; schemas/account.py exposes only
    `has_credentials: bool`.
    """

    __tablename__ = "owned_accounts"
    __table_args__ = (UniqueConstraint("platform", "handle", name="uq_owned_accounts_platform_handle"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[AccountPlatform] = mapped_column(Enum(AccountPlatform, native_enum=False, length=16))
    handle: Mapped[str] = mapped_column(String(200))
    # The platform's own numeric/opaque ID for this account - NOT derivable
    # from `handle` (a human-chosen display label). Real publishing needs
    # this for platforms whose API is node-based rather than "me"-relative
    # (e.g. Instagram Graph API's IG Business Account ID; see
    # publishing/providers/instagram_provider.py) - nullable because it
    # isn't needed for platforms where the access token alone identifies
    # the account, and because an account can be registered for health/
    # warmup tracking before this is known.
    platform_account_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    encrypted_oauth_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    niche_focus_id: Mapped[int | None] = mapped_column(ForeignKey("niches.id"), nullable=True, index=True)

    health_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Indexed from day one (PHASE1_AUDIT.md F6's lesson): filtered directly
    # by the Publishing Agent's health-tier gate once M4 lands.
    health_tier: Mapped[AccountHealthTier] = mapped_column(
        Enum(AccountHealthTier, native_enum=False, length=16), default=AccountHealthTier.HEALTHY, index=True
    )
    warmup_status: Mapped[AccountWarmupStatus] = mapped_column(
        Enum(AccountWarmupStatus, native_enum=False, length=16), default=AccountWarmupStatus.WARMING
    )
    daily_post_cap: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, native_enum=False, length=16), default=AccountStatus.ACTIVE
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"OwnedAccount(platform={self.platform}, handle={self.handle!r}, tier={self.health_tier})"


class AccountHealthSnapshot(TimestampMixin, Base):
    """ARCHITECTURE.md §8b — append-only history of every health check, so
    an account's health trend is visible over time, not just its latest
    value (which lives denormalized on OwnedAccount for fast reads)."""

    __tablename__ = "account_health_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("owned_accounts.id"), index=True)

    captured_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True))
    health_score: Mapped[float] = mapped_column(Float)
    tier: Mapped[AccountHealthTier] = mapped_column(Enum(AccountHealthTier, native_enum=False, length=16))
    posting_cadence_used: Mapped[int] = mapped_column(Integer)
    cap_utilization_pct: Mapped[float] = mapped_column(Float)
    engagement_trend: Mapped[float] = mapped_column(Float)
    strikes_count: Mapped[int] = mapped_column(Integer)
    api_error_rate: Mapped[float] = mapped_column(Float)

    def __repr__(self) -> str:  # pragma: no cover
        return f"AccountHealthSnapshot(account_id={self.account_id}, score={self.health_score}, tier={self.tier})"
