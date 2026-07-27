from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from content_factory.db.models.enums import ProcessingStatus
from content_factory.schemas.video import VideoOut


class ResearchRequest(BaseModel):
    # Bounded (v1.1, PHASE1_AUDIT.md F7) so operator-pasted competitor notes
    # can't unboundedly inflate the Research Agent's prompt size and cost —
    # 20k characters is generous for genuine notes, well short of anything
    # that would meaningfully help a real research brief.
    raw_notes: str = Field(default="", max_length=20_000)
    idempotency_key: str | None = Field(default=None, max_length=200)


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
    concept_summary: str = Field(max_length=2_000)
    predicted_score: float | None = Field(default=None, ge=0, le=1)
    source: str = Field(default="manual", max_length=50)


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
    # v1.1 (PHASE1_AUDIT.md F7): previously unbounded — a client could
    # request an arbitrarily large number of variants in one call, scaling
    # LLM cost with no server-side ceiling. 10 gives real headroom over
    # ARCHITECTURE.md's typical "3-5 variants" without allowing abuse.
    num_variants: int = Field(default=3, ge=1, le=10)
    idempotency_key: str | None = Field(default=None, max_length=200)


class ScriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    idea_id: int
    variant_label: str
    experiment_group: str | None
    hook_text: str
    hook_framework: str | None
    hook_strength_score: float | None
    full_text: str
    cta_text: str | None
    target_duration_s: int | None
    generation_status: ProcessingStatus
    created_at: datetime


class IdeaSelectRequest(BaseModel):
    num_variants: int = Field(default=3, ge=1, le=10)


class IdeaSelectionResult(BaseModel):
    """Result of the automatic Ideas -> Script -> Render cascade triggered
    by selecting an idea (the intentional human gate is *which* idea to
    select, not the mechanical steps after that choice — see
    api/routers/content.py::select_idea). `stage_reached` and `detail`
    exist specifically so a partial or empty result (no scripts generated,
    no video rendered) is reported honestly rather than looking like a
    silent success."""

    idea: ContentIdeaOut
    scripts: list[ScriptOut]
    video: VideoOut | None
    stage_reached: str
    detail: str
