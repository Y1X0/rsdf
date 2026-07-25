"""Shared enums for status/lifecycle fields.

Every long-running or agent-produced entity (ResearchBrief, Script, Video,
AgentRun, IdempotencyRecord) carries a `status` field using ProcessingStatus
plus request/completion timestamps. Phase 1 executes all of this
synchronously in the request/response cycle, but because the state lives in
the database rather than in memory, a Phase 2 background worker can simply
poll `WHERE status = 'pending'` — no schema change needed to introduce a
queue later.
"""

import enum


class ProcessingStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class CampaignStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ReviewDecisionType(str, enum.Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"


class VideoStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_RENDER = "pending_render"
    RENDERED = "rendered"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"
    PUBLISHED = "published"

    RENDER_FAILED = "render_failed"


class PatternConfidenceTier(str, enum.Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    RETIRED = "retired"


class HookSource(str, enum.Enum):
    INTERNAL = "internal"
    COMPETITOR_OBSERVED = "competitor_observed"


class PatternType(str, enum.Enum):
    HOOK = "hook"
    STRUCTURE = "structure"
    NICHE = "niche"
    TIMING = "timing"
