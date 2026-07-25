from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from content_factory.db.base import Base
from content_factory.db.models.enums import ReviewDecisionType
from content_factory.db.models.mixins import TimestampMixin


class ReviewDecision(TimestampMixin, Base):
    """ARCHITECTURE.md §7.1 — the human review workflow's audit trail.

    Append-only: a video can be reviewed more than once (e.g. rejected,
    revised, re-submitted, approved) and every decision is kept, not
    overwritten, so the reason-code aggregation in
    services/content_intelligence.py has full history to work from.
    """

    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)

    reviewer_id: Mapped[str] = mapped_column(String(100))
    decision: Mapped[ReviewDecisionType] = mapped_column(
        Enum(ReviewDecisionType, native_enum=False, length=32)
    )
    reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover
        return f"ReviewDecision(video_id={self.video_id}, decision={self.decision})"
