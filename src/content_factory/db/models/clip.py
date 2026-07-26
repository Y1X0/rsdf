from sqlalchemy import Enum, Float, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from content_factory.db.base import Base
from content_factory.db.models.enums import ClipStatus
from content_factory.db.models.mixins import TimestampMixin


class Clip(TimestampMixin, Base):
    """One AI-suggested highlight moment from a SourceVideo's transcript
    (start_s/end_s into the *source* video's timeline), produced by
    ClipSelectionAgent. Rendering a clip (services/clip_service.py) cuts
    the real source footage via the ffmpeg clip renderer and creates a
    `Video` row (`Video.clip_id`) — from that point on it's an ordinary
    Video and flows through the existing QC/review/publish pipeline
    unchanged; this row just keeps the link back to where it came from and
    which moment was actually selected before rendering.
    """

    __tablename__ = "clips"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_video_id: Mapped[int] = mapped_column(ForeignKey("source_videos.id"), index=True)

    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float] = mapped_column(Float)
    hook_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    predicted_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[ClipStatus] = mapped_column(
        Enum(ClipStatus, native_enum=False, length=32), default=ClipStatus.SUGGESTED, index=True
    )

    source_video: Mapped["SourceVideo"] = relationship(back_populates="clips")  # noqa: F821
    video: Mapped["Video | None"] = relationship(back_populates="clip", uselist=False)  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"Clip(id={self.id}, start_s={self.start_s}, end_s={self.end_s})"
