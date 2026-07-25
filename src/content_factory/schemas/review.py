from datetime import datetime

from pydantic import BaseModel, ConfigDict

from content_factory.db.models.enums import ReviewDecisionType


class ReviewSubmitRequest(BaseModel):
    reviewer_id: str
    decision: ReviewDecisionType
    reason_code: str | None = None
    notes: str | None = None


class ReviewDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    video_id: int
    reviewer_id: str
    decision: ReviewDecisionType
    reason_code: str | None
    notes: str | None
    decided_at: datetime
