from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from content_factory.db.models.enums import BudgetScope


class BudgetCeilingCreate(BaseModel):
    scope: BudgetScope
    niche_id: int | None = None
    monthly_limit_usd: float = Field(gt=0)


class BudgetCeilingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scope: BudgetScope
    niche_id: int | None
    monthly_limit_usd: float
    last_alert_threshold_pct: float | None
    created_at: datetime


class BudgetStatusOut(BaseModel):
    scope: BudgetScope
    niche_id: int | None
    monthly_limit_usd: float
    spend_usd: float
    pct_used: float
    is_blocked: bool
