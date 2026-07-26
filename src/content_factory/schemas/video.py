from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from content_factory.db.models.enums import ProcessingStatus, VideoStatus


class RenderRequestBody(BaseModel):
    template_id: str | None = Field(default=None, max_length=100)
    idempotency_key: str | None = Field(default=None, max_length=200)


class QualityScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    originality_score: float | None
    retention_prediction_score: float | None
    policy_risk_score: float | None
    monetization_probability_score: float | None
    model_version: str
    computed_at: datetime


class VideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    script_id: int | None
    clip_id: int | None
    status: VideoStatus
    template_id: str | None
    render_status: ProcessingStatus
    asset_url: str | None
    thumbnail_url: str | None
    duration_s: float | None
    voice_id: str | None
    caption_style: str | None
    contains_ai_voice: bool
    contains_ai_visual: bool
    qc_status: str | None
    qc_notes: str | None = None
    created_at: datetime
    quality_score: QualityScoreOut | None = None
