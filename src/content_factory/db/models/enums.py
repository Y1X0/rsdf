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


# --- Phase 2 additions below ---


class BudgetScope(str, enum.Enum):
    """ARCHITECTURE.md §10c — a budget ceiling applies either system-wide
    or to one specific niche; `niche_id` is null for system scope."""

    SYSTEM = "system"
    NICHE = "niche"


class NotificationChannel(str, enum.Enum):
    LOG = "log"
    SLACK = "slack"
    EMAIL = "email"


class NotificationSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AccountPlatform(str, enum.Enum):
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"


class AccountHealthTier(str, enum.Enum):
    """ARCHITECTURE.md §8b. A tier drop overrides the system-level
    automation phase for that specific account regardless of what the
    rest of the portfolio is doing."""

    HEALTHY = "healthy"
    WATCH = "watch"
    AT_RISK = "at_risk"
    RESTRICTED = "restricted"


class AccountWarmupStatus(str, enum.Enum):
    WARMING = "warming"
    ACTIVE = "active"


class AccountStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    SUSPENDED = "suspended"


class PublicationStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    FAILED = "failed"
    REMOVED = "removed"


class ExperimentAxis(str, enum.Enum):
    HOOK = "hook"
    NICHE = "niche"
    LENGTH = "length"
    POSTING_TIME = "posting_time"


class ExperimentStatus(str, enum.Enum):
    RUNNING = "running"
    CONCLUDED = "concluded"
    INCONCLUSIVE = "inconclusive"


class ExperimentSubjectType(str, enum.Enum):
    HOOK = "hook"
    NICHE = "niche"
    LENGTH_BUCKET = "length_bucket"
    POSTING_TIME_BUCKET = "posting_time_bucket"
