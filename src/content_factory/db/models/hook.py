from sqlalchemy import JSON, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from content_factory.db.base import Base
from content_factory.db.models.enums import HookSource, PatternConfidenceTier, PatternType
from content_factory.db.models.mixins import TimestampMixin


class HookLibrary(TimestampMixin, Base):
    """ARCHITECTURE.md §4.2 — the Viral Hook Database.

    `embedding` is intentionally omitted in Phase 1 (see the plan's "vector
    search deferred" decision) — retrieval in services/content_intelligence.py
    filters by niche/hook_type/score with plain SQL. Adding a pgvector column
    later is an additive migration; nothing here needs to change.

    v1.1 (PHASE1_AUDIT.md F5): the unique constraint on
    (niche_id, hook_text) is what backs
    services/content_intelligence.py's safe get-or-create — without it,
    concurrent calls could silently create duplicate rows for identical
    text. Per standard SQL NULL semantics, this does *not* dedupe hooks
    with niche_id IS NULL (each NULL is distinct); every current caller
    always supplies a niche_id, so this is an accepted, documented gap
    rather than a silent one.
    """

    __tablename__ = "hook_library"
    __table_args__ = (UniqueConstraint("niche_id", "hook_text", name="uq_hook_library_niche_id_hook_text"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    niche_id: Mapped[int | None] = mapped_column(ForeignKey("niches.id"), nullable=True, index=True)

    hook_text: Mapped[str] = mapped_column(Text)
    hook_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source: Mapped[HookSource] = mapped_column(
        Enum(HookSource, native_enum=False, length=32), default=HookSource.INTERNAL
    )

    best_viral_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    retention_at_3s: Mapped[float | None] = mapped_column(Float, nullable=True)
    times_used: Mapped[int] = mapped_column(Integer, default=0)

    def __repr__(self) -> str:  # pragma: no cover
        return f"HookLibrary(id={self.id}, source={self.source})"


class LearningPattern(TimestampMixin, Base):
    """ARCHITECTURE.md §4.3 — winning (and losing) whole-video pattern storage.

    `confidence_tier` starts at CANDIDATE for every new pattern. Promotion to
    CONFIRMED (or demotion to RETIRED) is the Experimentation Engine's job in
    Phase 2 (ARCHITECTURE.md §5); Phase 1's services/content_intelligence.py
    only ever *records* observed patterns and tags outcomes — it does not
    attempt statistical promotion, since Phase 1 volume can't support that
    (see ARCHITECTURE.md §5's honest caveat about small-sample significance).
    """

    __tablename__ = "learning_patterns"

    id: Mapped[int] = mapped_column(primary_key=True)
    niche_id: Mapped[int | None] = mapped_column(ForeignKey("niches.id"), nullable=True, index=True)

    pattern_type: Mapped[PatternType] = mapped_column(
        Enum(PatternType, native_enum=False, length=32)
    )
    description: Mapped[str] = mapped_column(Text)
    pattern_template_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    confidence_tier: Mapped[PatternConfidenceTier] = mapped_column(
        Enum(PatternConfidenceTier, native_enum=False, length=32),
        default=PatternConfidenceTier.CANDIDATE,
    )
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    supporting_video_ids: Mapped[list] = mapped_column(JSON, default=list)

    outcome_tag: Mapped[str | None] = mapped_column(String(50), nullable=True)
    """'known_good' | 'known_bad' | None — set by services/review_service.py
    when a reason code repeats (ARCHITECTURE.md §7.1) or by
    services/analytics_service.py once a viral score comes in."""

    def __repr__(self) -> str:  # pragma: no cover
        return f"LearningPattern(id={self.id}, tier={self.confidence_tier})"
