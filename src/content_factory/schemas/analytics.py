from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MetricsSubmitRequest(BaseModel):
    views: int = Field(ge=0)
    avg_watch_time_s: float | None = Field(default=None, ge=0)
    completion_rate: float | None = Field(default=None, ge=0, le=1)
    rewatch_rate: float | None = Field(default=None, ge=0, le=1)
    shares: int = Field(default=0, ge=0)
    comments: int = Field(default=0, ge=0)
    likes: int = Field(default=0, ge=0)
    saves: int = Field(default=0, ge=0)
    source: str = Field(default="manual", max_length=20)


class ViralScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    score: float
    breakdown_json: dict
    recommendation: str | None


class MetricsResponse(BaseModel):
    video_id: int
    views: int
    captured_at: datetime
    viral_score: ViralScoreOut


class CostSubmitRequest(BaseModel):
    category: str = Field(max_length=20)
    cost_usd: float = Field(ge=0)
    provider: str | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=300)


class CostEntryOut(BaseModel):
    id: int
    cost_usd: float


class RevenueSubmitRequest(BaseModel):
    campaign_id: int
    raw_views: int | None = Field(default=None, ge=0)
    approved_views: int | None = Field(default=None, ge=0)
    payout_realized: float = Field(default=0.0, ge=0)
    payout_pending: float = Field(default=0.0, ge=0)
    status: str = Field(default="pending", max_length=20)


class RevenueEntryOut(BaseModel):
    id: int
    payout_realized: float


class ProfitSummaryOut(BaseModel):
    total_cost_usd: float
    total_revenue_usd: float
    profit_usd: float
