from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from content_factory.db.models.enums import ReviewDecisionType


class ReviewSubmitRequest(BaseModel):
    """`reviewer_id` is optional and, if supplied, purely informational —
    v1.1 (PHASE1_AUDIT.md F2) derives the authoritative reviewer identity
    from the caller's authenticated JWT subject instead of trusting a
    client-supplied string, so nobody can "approve" content as someone
    else. The field is kept (rather than removed) only for backward
    compatibility with existing callers; the router logs a mismatch if the
    supplied value disagrees with the authenticated identity."""

    reviewer_id: str | None = None
    decision: ReviewDecisionType
    reason_code: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class ReviewDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    video_id: int
    reviewer_id: str
    decision: ReviewDecisionType
    reason_code: str | None
    notes: str | None
    decided_at: datetime
