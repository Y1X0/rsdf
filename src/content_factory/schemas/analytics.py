from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MetricsSubmitRequest(BaseModel):
    views: int
    avg_watch_time_s: float | None = None
    completion_rate: float | None = None
    rewatch_rate: float | None = None
    shares: int = 0
    comments: int = 0
    likes: int = 0
    saves: int = 0
    source: str = "manual"


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
    category: str
    cost_usd: float
    provider: str | None = None
    note: str | None = None


class RevenueSubmitRequest(BaseModel):
    campaign_id: int
    raw_views: int | None = None
    approved_views: int | None = None
    payout_realized: float = 0.0
    payout_pending: float = 0.0
    status: str = "pending"


class ProfitSummaryOut(BaseModel):
    total_cost_usd: float
    total_revenue_usd: float
    profit_usd: float
