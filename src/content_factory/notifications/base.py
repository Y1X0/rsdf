"""NotificationProvider — the interface every alerting channel sits behind
(budget-governor threshold alerts, future review-escalation alerts), same
shape as `llm/base.py`/`video_production/tts/base.py`: business logic
(services/budget_governor.py) depends only on this ABC, never on a
concrete Slack/SMTP client directly.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from content_factory.db.models.enums import NotificationSeverity


@dataclass(frozen=True)
class NotificationRequest:
    severity: NotificationSeverity
    subject: str
    body: str
    related_entity_type: str | None = None
    related_entity_id: int | None = None


@dataclass(frozen=True)
class NotificationResult:
    channel: str
    delivered: bool


class NotificationProvider(ABC):
    @abstractmethod
    def send(self, request: NotificationRequest) -> NotificationResult:
        raise NotImplementedError
