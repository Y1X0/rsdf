from datetime import datetime

from pydantic import BaseModel, ConfigDict

from content_factory.db.models.enums import HookSource, PatternConfidenceTier, PatternType


class HookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    niche_id: int | None
    hook_text: str
    hook_type: str | None
    source: HookSource
    best_viral_score: float | None
    retention_at_3s: float | None
    times_used: int
    created_at: datetime


class LearningPatternOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    niche_id: int | None
    pattern_type: PatternType
    description: str
    confidence_tier: PatternConfidenceTier
    confidence_score: float | None
    outcome_tag: str | None
    supporting_video_ids: list
    created_at: datetime
