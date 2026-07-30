"""Shared "safe get-or-create" helper (PHASE1_AUDIT.md F5: "two check-then-
act races with no database-level backstop"). A `SELECT` followed by an
`INSERT` races under concurrency — this wraps the insert in a SAVEPOINT so
a losing concurrent request falls back to the row the winner just created,
instead of surfacing an unhandled `IntegrityError` (previously an unlogged
500 in `_get_or_create_niche`, and a silently-possible duplicate row in
`find_or_create_hook`, which had no unique constraint to even race against
until this release added one).

Requires the target table to have a real unique constraint backing
`query`/`create` — this helper handles the *race*, not the uniqueness
itself.
"""

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

T = TypeVar("T")


def get_or_create(db: Session, *, query: Callable[[], T | None], create: Callable[[], T]) -> T:
    existing = query()
    if existing is not None:
        return existing

    try:
        with db.begin_nested():
            entity = create()
            db.flush()
        return entity
    except IntegrityError:
        existing = query()
        if existing is None:
            raise  # pragma: no cover - would mean the row vanished, not a race
        return existing
