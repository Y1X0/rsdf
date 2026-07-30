from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from content_factory.db.base import Base
from content_factory.db.models.enums import ProcessingStatus
from content_factory.db.models.mixins import TimestampMixin


class ResearchBrief(TimestampMixin, Base):
    """Output of the Research Agent for a campaign (ARCHITECTURE.md §2.1, §4).

    `raw_input_notes` holds whatever the human operator pasted in (competitor
    post transcripts, trend notes) — Phase 1 has no automated scraping (per
    ARCHITECTURE.md §0's Whop/TikTok API caveats), so the agent's job is to
    *structure* human-supplied raw material, not to go fetch it itself.
    `structured_data` is the agent's parsed output (competitor patterns,
    recommended angles) and is what services/content_intelligence.py reads
    to populate the hook library / learning patterns.
    """

    __tablename__ = "research_briefs"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)

    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, native_enum=False, length=32),
        default=ProcessingStatus.PENDING,
    )
    raw_input_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    brief_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)

    requested_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"ResearchBrief(campaign_id={self.campaign_id}, status={self.status})"


class ContentIdea(TimestampMixin, Base):
    __tablename__ = "content_ideas"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    concept_summary: Mapped[str] = mapped_column(Text)
    predicted_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="research_agent")
    status: Mapped[str] = mapped_column(String(50), default="proposed")

    scripts: Mapped[list["Script"]] = relationship(back_populates="idea")
    campaign: Mapped["Campaign"] = relationship()  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"ContentIdea(id={self.id}, campaign_id={self.campaign_id})"


class Script(TimestampMixin, Base):
    """A single script variant produced by the Script Agent.

    `experiment_group` is populated now (control/variant_a/variant_b/...)
    even though the statistical Experimentation Engine (ARCHITECTURE.md §5)
    that *acts* on experiment results is Phase 2+ — Phase 1 only needs to
    make sure the label exists on every row so no backfill migration is
    required later.
    """

    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("content_ideas.id"), index=True)

    variant_label: Mapped[str] = mapped_column(String(50))
    experiment_group: Mapped[str | None] = mapped_column(String(50), nullable=True)

    hook_text: Mapped[str] = mapped_column(Text)
    # Which named pattern from services/hook_scoring.py's HOOK_FRAMEWORKS
    # the LLM says it used (e.g. "curiosity_gap") - nullable since older
    # rows predate this, and a malformed/unrecognized value from the LLM
    # is simply not stored rather than rejected (see ScriptAgent).
    hook_framework: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Pre-publish heuristic score (0-100) from
    # hook_scoring.score_hook_strength() - a proxy computed at generation
    # time, independent of (and much earlier than) best_viral_score, which
    # only exists once real post-publish metrics come in.
    hook_strength_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    full_text: Mapped[str] = mapped_column(Text)
    cta_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)

    generation_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, native_enum=False, length=32),
        default=ProcessingStatus.COMPLETED,
    )
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)

    idea: Mapped["ContentIdea"] = relationship(back_populates="scripts")
    videos: Mapped[list["Video"]] = relationship(back_populates="script")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"Script(id={self.id}, variant={self.variant_label!r})"
