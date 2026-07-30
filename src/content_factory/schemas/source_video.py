from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from content_factory.db.models.enums import ProcessingStatus, SourceVideoOrigin


class SourceVideoCreate(BaseModel):
    title: str = Field(max_length=300)
    campaign_id: int | None = None


class SourceVideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int | None
    title: str
    duration_s: float | None
    source: SourceVideoOrigin
    external_source_id: str | None
    transcription_status: ProcessingStatus
    transcript_text: str | None
    analysis_status: ProcessingStatus
    created_at: datetime


class ContentRewardsSyncResultItem(BaseModel):
    external_id: str
    source_video_id: int
    created: bool  # False when an earlier sync already fetched this exact video


class ContentRewardsSyncResponse(BaseModel):
    results: list[ContentRewardsSyncResultItem]


class TranscribeRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, max_length=200)


class AnalyzeRequest(BaseModel):
    max_clips: int = Field(default=5, ge=1, le=20)
    idempotency_key: str | None = Field(default=None, max_length=200)
