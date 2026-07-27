from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from content_factory.db.models.enums import ClipStatus


class ClipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_video_id: int
    start_s: float
    end_s: float
    hook_text: str | None
    hook_framework: str | None
    hook_strength_score: float | None
    predicted_score: float | None
    reason: str | None
    status: ClipStatus
    video_id: int | None
    created_at: datetime


class ClipRenderRequestBody(BaseModel):
    idempotency_key: str | None = Field(default=None, max_length=200)
