from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from content_factory.db.models.enums import CampaignStatus, ProcessingStatus


class CampaignCreate(BaseModel):
    """`idempotency_key` is optional — if omitted, a fingerprint of the rest
    of this payload is used instead (see services/idempotency.py), so
    accidentally submitting the same campaign twice still can't create a
    duplicate.

    Field bounds (v1.1, PHASE1_AUDIT.md F7) mirror the underlying column
    widths in db/models/campaign.py so an oversized request fails fast with
    a clear 422 instead of surfacing as a database error, and so a large
    `rules_text` can't silently balloon the Research Agent's prompt size
    (and cost) with no server-side ceiling.
    """

    whop_campaign_id: str | None = Field(default=None, max_length=200)
    brand_name: str = Field(max_length=200)
    niche_name: str | None = Field(default=None, max_length=200)
    payout_model: str | None = Field(default=None, max_length=100)
    cpm_rate: float | None = Field(default=None, ge=0)
    budget_cap: float | None = Field(default=None, ge=0)
    rules_text: str | None = Field(default=None, max_length=10_000)
    ai_content_allowed: bool = True
    idempotency_key: str | None = Field(default=None, max_length=200)


class CampaignScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    status: ProcessingStatus
    expected_roi_low: float | None
    expected_roi_median: float | None
    expected_roi_high: float | None
    difficulty_score: float | None
    competition_level: float | None
    niche_fit_score: float | None
    composite_score: float | None
    recommendation: str | None
    breakdown_json: dict | None
    created_at: datetime


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    whop_campaign_id: str | None
    brand_name: str
    niche_id: int | None
    payout_model: str | None
    cpm_rate: float | None
    budget_cap: float | None
    rules_text: str | None
    ai_content_allowed: bool
    status: CampaignStatus
    created_at: datetime
    latest_score: CampaignScoreOut | None = None
