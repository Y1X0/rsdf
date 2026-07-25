"""Import every model module so Base.metadata is fully populated — required
for Alembic autogeneration and for Base.metadata.create_all() in tests."""

from content_factory.db.models.account import AccountHealthSnapshot, OwnedAccount
from content_factory.db.models.agent_run import AgentRun
from content_factory.db.models.analytics import CostLedger, MetricsSnapshot, RevenueSnapshot, ViralScoreRecord
from content_factory.db.models.budget import BudgetCeiling, ProviderQuotaUsage
from content_factory.db.models.campaign import Campaign, CampaignScore
from content_factory.db.models.content import ContentIdea, ResearchBrief, Script
from content_factory.db.models.experiment import Experiment, ExperimentResult
from content_factory.db.models.hook import HookLibrary, LearningPattern
from content_factory.db.models.idempotency import IdempotencyRecord
from content_factory.db.models.niche import Niche
from content_factory.db.models.notification import NotificationLog
from content_factory.db.models.publication import Publication
from content_factory.db.models.quality import QualityScore
from content_factory.db.models.review import ReviewDecision
from content_factory.db.models.video import Video

__all__ = [
    "AccountHealthSnapshot",
    "AgentRun",
    "BudgetCeiling",
    "Campaign",
    "CampaignScore",
    "ContentIdea",
    "CostLedger",
    "Experiment",
    "ExperimentResult",
    "HookLibrary",
    "IdempotencyRecord",
    "LearningPattern",
    "MetricsSnapshot",
    "Niche",
    "NotificationLog",
    "OwnedAccount",
    "Publication",
    "ProviderQuotaUsage",
    "QualityScore",
    "ResearchBrief",
    "RevenueSnapshot",
    "ReviewDecision",
    "Script",
    "Video",
    "ViralScoreRecord",
]
