from sqlalchemy import JSON, DateTime, Enum, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from content_factory.db.base import Base
from content_factory.db.models.enums import ProcessingStatus
from content_factory.db.models.mixins import TimestampMixin


class AgentRun(TimestampMixin, Base):
    """The single versioned log of every AI-generated output in the system.

    Adjustment #3 ("every AI output must be versioned: prompt, model, model
    version, timestamp, cost, duration") and adjustment #4 ("every agent
    must produce structured logs, no silent failures, every decision
    traceable") are both implemented through this one table rather than
    bespoke per-agent logging, because every agent and every external
    provider call (LLM, TTS, render) needs the exact same fields. See
    agents/base.py's `AgentRunRecorder`, which is the only way any of these
    rows get written — no agent or service writes to this table directly,
    so the versioning discipline can't be accidentally skipped.

    This table is also the audit trail referenced in ARCHITECTURE.md §12/§17.4
    (append-only, never edited after `status` leaves IN_PROGRESS).
    """

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)

    agent_name: Mapped[str] = mapped_column(String(100), index=True)
    scope: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, native_enum=False, length=32),
        default=ProcessingStatus.IN_PROGRESS,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"AgentRun(agent={self.agent_name!r}, status={self.status}, scope={self.scope!r})"
