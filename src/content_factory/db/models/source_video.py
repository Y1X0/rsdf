from sqlalchemy import JSON, Enum, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from content_factory.db.base import Base
from content_factory.db.models.enums import ProcessingStatus, SourceVideoOrigin
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

    `transcript_words` is the same idea at word granularity
    (`[{"start": float, "end": float, "word": str}, ...]`), used by
    ClipRenderer to burn captions tightly synced to the moment each word
    is actually spoken rather than showing a whole multi-second segment's
    text all at once. Optional (nullable, defaults empty on read): a
    provider that can't supply word-level timing, or a video transcribed
    before this existed, simply has none — callers fall back to
    segment-level captions, never a hard requirement.

    `speaker_turns` (`[{"start": float, "end": float, "speaker": str}, ...]`)
    is *who* is talking during each stretch, from a real (optional, heavy)
    diarization provider — see diarization/base.py. Optional in the exact
    same sense as transcript_words: the default NullDiarizationProvider
    never populates it, so it's simply absent unless a deployment has
    explicitly opted into real diarization; ClipRenderer falls back to
    single-speaker caption styling whenever it's empty.

    `source`/`external_source_id` (content_sources/, additive) record where
    the file came from: `"upload"` (the existing manual multipart upload,
    `external_source_id` always NULL) or `"content_rewards"` (fetched via
    `POST /source-videos/sync-content-rewards`, `external_source_id` is that
    platform's own video/campaign id — the dedup key a re-sync uses so the
    same remote video is never downloaded twice). Purely a provenance/dedup
    concern: `transcribe_source_video`/`analyze_source_video`/`render_clip`
    read only `storage_path` and never look at either of these two columns.
    """

    __tablename__ = "source_videos"
    __table_args__ = (UniqueConstraint("source", "external_source_id", name="uq_source_videos_source_external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True, index=True)

    title: Mapped[str] = mapped_column(String(300))
    storage_path: Mapped[str] = mapped_column(String(500))
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[SourceVideoOrigin] = mapped_column(
        Enum(SourceVideoOrigin, native_enum=False, length=32), default=SourceVideoOrigin.UPLOAD
    )
    external_source_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    transcription_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, native_enum=False, length=32), default=ProcessingStatus.PENDING
    )
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_segments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    transcript_words: Mapped[list | None] = mapped_column(JSON, nullable=True)
    speaker_turns: Mapped[list | None] = mapped_column(JSON, nullable=True)
    transcription_agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True
    )

    analysis_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, native_enum=False, length=32), default=ProcessingStatus.PENDING
    )
    analysis_agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)

    clips: Mapped[list["Clip"]] = relationship(back_populates="source_video")  # noqa: F821
    campaign: Mapped["Campaign | None"] = relationship()  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"SourceVideo(id={self.id}, title={self.title!r})"
