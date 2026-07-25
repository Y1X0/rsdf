from pydantic import BaseModel


class DashboardSummaryOut(BaseModel):
    campaign_count: int
    video_counts_by_status: dict[str, int]
    pending_review_count: int
    total_cost_usd: float
    total_revenue_usd: float
    profit_usd: float
