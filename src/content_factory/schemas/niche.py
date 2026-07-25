"""v1.1 addition (PHASE1_AUDIT.md F3 — "Niche management is a dead end"):
previously there was no way to read or write a niche's
saturation/CPM/trend fields through the API at all; Campaign Intelligence
scoring (services/campaign_scoring.py) could only ever see the neutral
0.5 default. `NicheUpdate` is the write path that was missing."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NicheCreate(BaseModel):
    name: str = Field(max_length=200)
    category: str | None = Field(default=None, max_length=200)
    saturation_score: float | None = Field(default=None, ge=0, le=1)
    avg_cpm_est: float | None = Field(default=None, ge=0)
    trend_score: float | None = Field(default=None, ge=0, le=1)


class NicheUpdate(BaseModel):
    """All fields optional — a partial update. `None` (the default) means
    "leave unchanged," not "clear the value"; there's no way to un-set a
    field back to null through this endpoint today, which is an acceptable
    Phase 1 limitation (a real operator scenario is always "I now have a
    better estimate," never "I want to forget the one I had")."""

    category: str | None = Field(default=None, max_length=200)
    saturation_score: float | None = Field(default=None, ge=0, le=1)
    avg_cpm_est: float | None = Field(default=None, ge=0)
    trend_score: float | None = Field(default=None, ge=0, le=1)


class NicheOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str | None = None
    saturation_score: float | None = None
    avg_cpm_est: float | None = None
    trend_score: float | None = None
    created_at: datetime
