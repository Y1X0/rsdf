from sqlalchemy import Enum, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from content_factory.db.base import Base
from content_factory.db.models.enums import ProcessingStatus
from content_factory.db.models.mixins import TimestampMixin


class IdempotencyRecord(TimestampMixin, Base):
    """Backs services/idempotency.py.

    Adjustment #5: "the same campaign, script, or render request should
    never create duplicate work." A (scope, key) pair identifies one
    logical request; `request_fingerprint` is a hash of the request's
    business-relevant fields, so replaying the same key with *different*
    parameters is treated as an error rather than silently returning stale
    results. `result_entity_type`/`result_entity_id` point at whatever row
    the work produced (a Campaign, a Script, a Video, ...), so a repeat
    request can return the existing entity instead of redoing paid work
    (an LLM call, a render, ...).
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(100), index=True)
    key: Mapped[str] = mapped_column(String(200), index=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))

    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, native_enum=False, length=32),
        default=ProcessingStatus.IN_PROGRESS,
    )
    result_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    result_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"IdempotencyRecord(scope={self.scope!r}, key={self.key!r}, status={self.status})"
