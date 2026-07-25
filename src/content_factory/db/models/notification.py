from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from content_factory.db.base import Base
from content_factory.db.models.enums import NotificationChannel, NotificationSeverity
from content_factory.db.models.mixins import TimestampMixin


class NotificationLog(TimestampMixin, Base):
    """Append-only record of every notification the system has sent (or, for
    the default `log` channel, would have sent) — both an audit trail and
    what backs the always-available `LogNotificationProvider` default (see
    notifications/providers/log_provider.py). `related_entity_type`/
    `related_entity_id` link back to whatever triggered it (a BudgetCeiling,
    a ReviewDecision, ...), following the same loose polymorphic-reference
    convention already used by AgentRun and CostLedger.
    """

    __tablename__ = "notifications_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[NotificationChannel] = mapped_column(
        Enum(NotificationChannel, native_enum=False, length=16)
    )
    severity: Mapped[NotificationSeverity] = mapped_column(
        Enum(NotificationSeverity, native_enum=False, length=16)
    )
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True))
    related_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    related_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"NotificationLog(channel={self.channel}, subject={self.subject!r})"
