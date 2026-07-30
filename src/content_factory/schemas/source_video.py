from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from content_factory.db.models.enums import ProcessingStatus


class SourceVideoCreate(BaseModel):
    title: str = Field(max_length=300)
    campaign_id: int | None = None


class SourceVideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int | None
    title: str
    duration_s: float | None
    transcription_status: ProcessingStatus
    transcript_text: str | None
    analysis_status: ProcessingStatus
    created_at: datetime


class TranscribeRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, max_length=200)


class AnalyzeRequest(BaseModel):
    max_clips: int = Field(default=5, ge=1, le=20)
    idempotency_key: str | None = Field(default=None, max_length=200)
