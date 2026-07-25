from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from content_factory.db.models.enums import ExperimentAxis, ExperimentStatus, ExperimentSubjectType
from content_factory.services.experimentation_service import (
    DEFAULT_MIN_SAMPLE_SIZE,
    DEFAULT_SIGNIFICANCE_THRESHOLD,
)


class RunExperimentRequest(BaseModel):
    axis: ExperimentAxis
    niche_id: int | None = None
    min_sample_size: int = Field(default=DEFAULT_MIN_SAMPLE_SIZE, ge=1, le=10_000)
    significance_threshold: float = Field(default=DEFAULT_SIGNIFICANCE_THRESHOLD, ge=0, le=10)


class ExperimentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    axis: ExperimentAxis
    niche_id: int | None
    status: ExperimentStatus
    min_sample_size: int
    significance_threshold: float
    started_at: datetime
    concluded_at: datetime | None


class ExperimentResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    experiment_id: int
    subject_type: ExperimentSubjectType
    subject_key: str
    sample_size: int
    avg_viral_score: float
    is_winner: bool
    applied_at: datetime | None
    applied_by: str | None
    computed_at: datetime


class RunExperimentResponse(BaseModel):
    experiment: ExperimentOut
    results: list[ExperimentResultOut]


class ApplyRecommendationRequest(BaseModel):
    applied_by: str = Field(max_length=100)
