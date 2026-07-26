from sqlalchemy import JSON, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from content_factory.db.base import Base
from content_factory.db.models.enums import ProcessingStatus
from content_factory.db.models.mixins import TimestampMixin


class SourceVideo(TimestampMixin, Base):
    """A long-form video uploaded by the operator to be turned into short
    clips (the "montage" pipeline) — distinct from Script/Video, which are
    generated from scratch by the Research/Script agents. `storage_path` is
    a local-disk path under MEDIA_STORAGE_DIR, matching how rendered videos
    are stored (services/production_service.py), not a remote URL.

    `transcript_segments` is the timestamped transcript
    (`[{"start": float, "end": float, "text": str}, ...]`) used by
    ClipSelectionAgent to pick moments — kept as JSON rather than a
    separate table since it's always read/written as one whole unit per
    source video, never queried by individual segment.
    """

    __tablename__ = "source_videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True, index=True)

    title: Mapped[str] = mapped_column(String(300))
    storage_path: Mapped[str] = mapped_column(String(500))
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    transcription_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, native_enum=False, length=32), default=ProcessingStatus.PENDING
    )
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_segments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    transcription_agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True
    )

    analysis_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, native_enum=False, length=32), default=ProcessingStatus.PENDING
    )
    analysis_agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)

    clips: Mapped[list["Clip"]] = relationship(back_populates="source_video")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"SourceVideo(id={self.id}, title={self.title!r})"
