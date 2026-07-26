from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from content_factory.db.base import Base
from content_factory.db.models.enums import AccountPlatform, PublicationStatus
from content_factory.db.models.mixins import TimestampMixin


class Publication(TimestampMixin, Base):
    """ARCHITECTURE.md §2.4 — one row per publish attempt. A new attempt
    (e.g. a retried idempotency key) creates a new row rather than mutating
    an old one, matching production_service.py's Video-row precedent — old
    FAILED rows stay as audit history instead of being overwritten.
    """

    __tablename__ = "publications"
    __table_args__ = (
        # Production Hardening Sprint H5: covers
        # publishing_service.py's daily-cadence-cap check (filters
        # account_id + status + published_at >= start_of_day on every
        # publish attempt) as one index rather than three separate ones.
        # Its leftmost prefix (account_id) also covers
        # analytics_service.py's account-only lookup, so the standalone
        # single-column indexes account_id/status previously had here are
        # dropped as redundant.
        Index("ix_publications_account_status_published", "account_id", "status", "published_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("owned_accounts.id"))
    platform: Mapped[AccountPlatform] = mapped_column(Enum(AccountPlatform, native_enum=False, length=16))

    external_post_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    hashtags: Mapped[list] = mapped_column(JSON, default=list)

    scheduled_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[PublicationStatus] = mapped_column(
        Enum(PublicationStatus, native_enum=False, length=16), default=PublicationStatus.SCHEDULED
    )
    publish_agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Publication(video_id={self.video_id}, account_id={self.account_id}, status={self.status})"
