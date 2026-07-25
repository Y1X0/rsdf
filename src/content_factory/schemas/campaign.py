from datetime import datetime

from pydantic import BaseModel, ConfigDict

from content_factory.db.models.enums import CampaignStatus, ProcessingStatus


class NicheOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str | None = None
    saturation_score: float | None = None
    avg_cpm_est: float | None = None
    trend_score: float | None = None


class CampaignCreate(BaseModel):
    """`idempotency_key` is optional — if omitted, a fingerprint of the rest
    of this payload is used instead (see services/idempotency.py), so
    accidentally submitting the same campaign twice still can't create a
    duplicate."""

    whop_campaign_id: str | None = None
    brand_name: str
    niche_name: str | None = None
    payout_model: str | None = None
    cpm_rate: float | None = None
    budget_cap: float | None = None
    rules_text: str | None = None
    ai_content_allowed: bool = True
    idempotency_key: str | None = None


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
