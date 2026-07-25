from datetime import datetime

from pydantic import BaseModel, ConfigDict

from content_factory.db.models.enums import ProcessingStatus


class ResearchRequest(BaseModel):
    raw_notes: str = ""
    idempotency_key: str | None = None


class ResearchBriefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    status: ProcessingStatus
    brief_text: str | None
    structured_data: dict | None
    requested_at: datetime
    completed_at: datetime | None


class ContentIdeaCreate(BaseModel):
    concept_summary: str
    predicted_score: float | None = None
    source: str = "manual"


class ContentIdeaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    concept_summary: str
    predicted_score: float | None
    source: str
    status: str
    created_at: datetime


class ScriptGenerateRequest(BaseModel):
    num_variants: int = 3
    idempotency_key: str | None = None


class ScriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    idea_id: int
    variant_label: str
    experiment_group: str | None
    hook_text: str
    full_text: str
    cta_text: str | None
    target_duration_s: int | None
    generation_status: ProcessingStatus
    created_at: datetime
