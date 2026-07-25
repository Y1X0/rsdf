from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from content_factory.db.base import Base
from content_factory.db.models.enums import ProcessingStatus, VideoStatus
from content_factory.db.models.mixins import TimestampMixin


class Video(TimestampMixin, Base):
    """A rendered video produced from a Script via the template-based
    production pipeline (ARCHITECTURE.md §2.3, adjustment #2).

    `status` tracks the whole content-item lifecycle relevant to Phase 1
    (draft -> pending_render -> rendered -> pending_review -> approved /
    rejected / revision_requested -> published), matching the state machine
    in ARCHITECTURE.md §1.2. `render_status` is the narrower, technical
    status of just the render step, kept separate because a render can fail
    and be retried without the whole content item needing a new row — and
    because it, combined with render_requested_at/render_completed_at, is
    exactly the queue-friendly shape adjustment #7 asked for.
    """

    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("scripts.id"), index=True)

    # v1.1 (PHASE1_AUDIT.md F6): indexed — filtered directly in
    # list_pending_review's WHERE clause and grouped by in the dashboard
    # summary; this was a real missing index on a live query path, not a
    # forward-looking one.
    status: Mapped[VideoStatus] = mapped_column(
        Enum(VideoStatus, native_enum=False, length=32), default=VideoStatus.DRAFT, index=True
    )

    template_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    render_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, native_enum=False, length=32),
        default=ProcessingStatus.PENDING,
    )
    render_requested_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    render_completed_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    asset_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    voice_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    caption_style: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contains_ai_voice: Mapped[bool] = mapped_column(Boolean, default=True)
    contains_ai_visual: Mapped[bool] = mapped_column(Boolean, default=False)

    qc_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    qc_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    tts_agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    render_agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True
    )

    script: Mapped["Script"] = relationship(back_populates="videos")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"Video(id={self.id}, status={self.status})"
