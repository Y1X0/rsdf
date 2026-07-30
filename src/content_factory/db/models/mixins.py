from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    """created_at/updated_at present on every table.

    Kept as a mixin (not repeated per model) so every future table gets
    these for free, and so a Phase 2 background worker can always answer
    "when did this row last change" without a per-table special case.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
