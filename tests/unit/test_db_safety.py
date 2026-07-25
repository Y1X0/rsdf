"""Regression tests for P1-6 (PHASE1_AUDIT.md F5 — two check-then-act
races with no database-level backstop): db_safety.get_or_create is the
shared safe-upsert helper backing both the niche and hook get-or-create
paths."""

import pytest
from sqlalchemy import Column, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase

from content_factory.services import db_safety


class _Base(DeclarativeBase):
    pass


class _Widget(_Base):
    __tablename__ = "test_db_safety_widgets"
    __table_args__ = (UniqueConstraint("name", name="uq_test_widget_name"),)

    id = Column(Integer, primary_key=True)
    name = Column(String(100))


@pytest.fixture()
def widget_session(db_session):
    _Base.metadata.create_all(bind=db_session.get_bind())
    return db_session


def test_get_or_create_returns_existing_row_without_calling_create(widget_session):
    widget_session.add(_Widget(name="existing"))
    widget_session.flush()

    create_called = {"count": 0}

    def _create():
        create_called["count"] += 1
        w = _Widget(name="existing")
        widget_session.add(w)
        return w

    result = db_safety.get_or_create(
        widget_session,
        query=lambda: widget_session.query(_Widget).filter(_Widget.name == "existing").one_or_none(),
        create=_create,
    )
    assert create_called["count"] == 0
    assert result.name == "existing"


def test_get_or_create_creates_when_nothing_exists(widget_session):
    result = db_safety.get_or_create(
        widget_session,
        query=lambda: widget_session.query(_Widget).filter(_Widget.name == "brand_new").one_or_none(),
        create=lambda: _make_and_add(widget_session, "brand_new"),
    )
    assert result.id is not None
    assert result.name == "brand_new"


def test_get_or_create_falls_back_to_winner_on_concurrent_insert_race(widget_session):
    """Simulates the race directly: a row with the same unique key already
    exists in the database (as if a concurrent request just committed it),
    but the in-memory `query()` closure is written as if it hadn't been
    seen yet — the SAVEPOINT-guarded insert must hit the unique constraint
    and gracefully fall back to the pre-existing row instead of raising."""
    winner = _Widget(name="raced_name")
    widget_session.add(winner)
    widget_session.flush()

    call_count = {"n": 0}

    def _query():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None  # first check: pretend we haven't seen it yet
        return widget_session.query(_Widget).filter(_Widget.name == "raced_name").one_or_none()

    def _create():
        w = _Widget(name="raced_name")  # collides with `winner` on the unique constraint
        widget_session.add(w)
        return w

    result = db_safety.get_or_create(widget_session, query=_query, create=_create)
    assert result.id == winner.id


def _make_and_add(session, name: str) -> _Widget:
    widget = _Widget(name=name)
    session.add(widget)
    return widget
