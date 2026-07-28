from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from content_factory.db.models.enums import AccountHealthTier, AccountPlatform, AccountStatus, AccountWarmupStatus


class OwnedAccountCreate(BaseModel):
    platform: AccountPlatform
    handle: str = Field(max_length=200)
    # The platform's own numeric/opaque ID for this account - required for
    # real publishing on node-based APIs (Instagram Graph API's IG Business
    # Account ID; "me" does not resolve to it). Optional here since an
    # account can be registered for health/warmup tracking first.
    platform_account_id: str | None = Field(default=None, max_length=200)
    niche_focus_id: int | None = None
    daily_post_cap: int = Field(default=1, ge=1, le=50)
    # Plaintext input only — never stored or echoed back as-is. Optional:
    # an account can be registered (for health/warmup tracking) before any
    # real OAuth connection exists.
    oauth_token: str | None = Field(default=None, max_length=4_000)


class OwnedAccountUpdate(BaseModel):
    """All fields optional — a partial update, matching NicheUpdate's
    convention. `warmup_status` can only ever move warming -> active, and
    only when services/account_service.check_warmup_graduation_eligible
    allows it (enforced by the router, not just documented here)."""

    platform_account_id: str | None = Field(default=None, max_length=200)
    niche_focus_id: int | None = None
    daily_post_cap: int | None = Field(default=None, ge=1, le=50)
    status: AccountStatus | None = None
    warmup_status: AccountWarmupStatus | None = None
    oauth_token: str | None = Field(default=None, max_length=4_000)


class OwnedAccountOut(BaseModel):
    id: int
    platform: AccountPlatform
    handle: str
    platform_account_id: str | None
    has_credentials: bool
    niche_focus_id: int | None
    health_score: float | None
    health_tier: AccountHealthTier
    warmup_status: AccountWarmupStatus
    daily_post_cap: int
    status: AccountStatus
    created_at: datetime
    updated_at: datetime


class AccountHealthCheckRequest(BaseModel):
    # Bounded generously — this is an operator/cron-supplied observation,
    # not a cost-sensitive AI input, but still shouldn't be unbounded.
    posting_cadence_used: int = Field(ge=0, le=1_000)
    engagement_trend: float = Field(ge=-1.0, le=1.0)
    strikes_count: int = Field(default=0, ge=0, le=100)
    api_error_rate: float = Field(default=0.0, ge=0.0, le=1.0)


class AccountHealthSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    captured_at: datetime
    health_score: float
    tier: AccountHealthTier
    posting_cadence_used: int
    cap_utilization_pct: float
    engagement_trend: float
    strikes_count: int
    api_error_rate: float
