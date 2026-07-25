from datetime import datetime

from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from content_factory.db.base import Base
from content_factory.db.models.mixins import TimestampMixin


class Niche(TimestampMixin, Base):
    """A content niche (e.g. "personal finance", "gaming clips").

    Saturation/CPM/trend scores start null and are filled in manually in
    Phase 1 (or by the Research Agent's brief); Phase 2's Experimentation
    Engine (ARCHITECTURE.md §5) is what keeps these continuously updated.
    """

    __tablename__ = "niches"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    saturation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_cpm_est: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Phase 2 M6 (Experimentation Engine's niche axis) writes this; null
    # means "not yet computed, treat as equal split" — added in M2's
    # migration since it's a trivial additive column with no behavior
    # depending on it yet.
    allocation_weight: Mapped[float | None] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return f"Niche(id={self.id}, name={self.name!r})"
