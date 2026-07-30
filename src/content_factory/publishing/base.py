"""PublishingProvider — the interface every platform posting integration
sits behind (ARCHITECTURE.md §2.4), same shape as
video_production/tts/base.py and video_production/renderer/base.py:
business logic (services/publishing_service.py) depends only on this ABC,
never on a concrete TikTok/YouTube/Instagram client directly.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class PublishRequest:
    video_id: int
    asset_url: str
    title: str
    description: str
    hashtags: list[str] = field(default_factory=list)
    contains_ai_voice: bool = False
    contains_ai_visual: bool = False
    scheduled_at: datetime | None = None


@dataclass(frozen=True)
class PublishResult:
    provider: str
    # False for the manual provider (a human still has to actually post it —
    # this call only readies it); True once a real platform call has
    # actually published the content.
    published: bool
    external_post_id: str | None = None


class PublishingProvider(ABC):
    @abstractmethod
    def publish(self, request: PublishRequest) -> PublishResult:
        raise NotImplementedError
