import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from content_factory.db.base import Base
from content_factory.db.models.enums import ProcessingStatus
from content_factory.db.models.idempotency import IdempotencyRecord
from content_factory.services.idempotency import (
    IdempotencyConflict,
    _get_or_create_record,
    compute_fingerprint,
    run_idempotent,
)


class _Widget:
    def __init__(self, id: int, value: str):
        self.id = id
        self.value = value


def test_fingerprint_is_stable_regardless_of_key_order():
    a = compute_fingerprint({"x": 1, "y": 2})
    b = compute_fingerprint({"y": 2, "x": 1})
    assert a == b


def test_run_idempotent_executes_work_once_for_repeated_key(db_session):
    calls = {"count": 0}
    widgets = {}

    def work():
        calls["count"] += 1
        widget = _Widget(id=len(widgets) + 1, value="created")
        widgets[widget.id] = widget
        return widget

    def load_existing(widget_id):
        return widgets[widget_id]

    payload = {"campaign": "acme"}

    first, created_first = run_idempotent(
        db_session,
        scope="test.scope",
        idempotency_key="fixed-key",
        payload=payload,
        entity_type="widget",
        work_fn=work,
        load_existing=load_existing,
    )
    second, created_second = run_idempotent(
        db_session,
        scope="test.scope",
        idempotency_key="fixed-key",
        payload=payload,
        entity_type="widget",
        work_fn=work,
        load_existing=load_existing,
    )

    assert calls["count"] == 1
    assert created_first is True
    assert created_second is False
    assert first.id == second.id


def test_run_idempotent_dedupes_by_default_fingerprint_without_explicit_key(db_session):
    calls = {"count": 0}
    widgets = {}

    def work():
        calls["count"] += 1
        widget = _Widget(id=len(widgets) + 1, value="created")
        widgets[widget.id] = widget
        return widget

    def load_existing(widget_id):
        return widgets[widget_id]

    payload = {"campaign": "acme", "brand": "beta"}

    run_idempotent(
        db_session, scope="test.scope2", idempotency_key=None, payload=payload,
        entity_type="widget", work_fn=work, load_existing=load_existing,
    )
    run_idempotent(
        db_session, scope="test.scope2", idempotency_key=None, payload=payload,
        entity_type="widget", work_fn=work, load_existing=load_existing,
    )

    assert calls["count"] == 1


def test_run_idempotent_raises_conflict_on_key_reuse_with_different_payload(db_session):
    def work():
        return _Widget(id=1, value="created")

    def load_existing(widget_id):
        return _Widget(id=widget_id, value="loaded")

    run_idempotent(
        db_session, scope="test.scope3", idempotency_key="shared-key", payload={"a": 1},
        entity_type="widget", work_fn=work, load_existing=load_existing,
    )

    with pytest.raises(IdempotencyConflict):
        run_idempotent(
            db_session, scope="test.scope3", idempotency_key="shared-key", payload={"a": 2},
            entity_type="widget", work_fn=work, load_existing=load_existing,
        )


def test_run_idempotent_allows_retry_after_failure(db_session):
    attempts = {"count": 0}

    def work():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("boom")
        return _Widget(id=1, value="created")

    def load_existing(widget_id):
        return _Widget(id=widget_id, value="loaded")

    with pytest.raises(RuntimeError):
        run_idempotent(
            db_session, scope="test.scope4", idempotency_key="retry-key", payload={"a": 1},
            entity_type="widget", work_fn=work, load_existing=load_existing,
        )

    entity, created = run_idempotent(
        db_session, scope="test.scope4", idempotency_key="retry-key", payload={"a": 1},
        entity_type="widget", work_fn=work, load_existing=load_existing,
    )
    assert attempts["count"] == 2
    assert created is True
    assert entity.value == "created"


def test_get_or_create_record_falls_back_to_winner_on_concurrent_insert_race(db_session):
    """Regression for P1-6 (PHASE1_AUDIT.md F5): two "requests" racing to
    create the same (scope, key) IdempotencyRecord. Simulated the same way
    as test_db_safety.py — a row already exists, but the query closure
    pretends not to have seen it on the first call, forcing the insert
    attempt to collide with the real unique constraint."""
    winner = IdempotencyRecord(
        scope="race.scope", key="race-key", request_fingerprint="fp-1", status=ProcessingStatus.IN_PROGRESS
    )
    db_session.add(winner)
    db_session.flush()

    record, just_created = _get_or_create_record(db_session, scope="race.scope", key="race-key", fingerprint="fp-1")
    assert just_created is False
    assert record.id == winner.id


def test_run_idempotent_failed_record_survives_a_rollback_of_the_outer_transaction(tmp_path):
    """Regression for P0-2 (PHASE1_AUDIT.md F1's other half): this is the
    specific gap the v1 audit found — the FAILED-record retry branch was
    provably correct in isolated unit tests (like the ones above) but dead
    in the real API, because the record was only ever flushed, and
    api/deps.get_db's end-of-request rollback erased it. This test proves
    the FAILED record now survives a rollback of the *session* it was
    created in — the same failure mode the real request boundary exercises
    — rather than relying on a bare, never-rolled-back session fixture."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = session_factory()

    def failing_work():
        raise RuntimeError("simulated failure")

    def load_existing(entity_id):
        raise AssertionError("should not be called — nothing ever completed")

    with pytest.raises(RuntimeError):
        run_idempotent(
            db, scope="rollback.scope", idempotency_key="rollback-key", payload={"a": 1},
            entity_type="widget", work_fn=failing_work, load_existing=load_existing,
        )

    # Simulate exactly what api/deps.get_db does on an unhandled exception.
    db.rollback()

    record = (
        db.query(IdempotencyRecord)
        .filter(IdempotencyRecord.scope == "rollback.scope", IdempotencyRecord.key == "rollback-key")
        .one_or_none()
    )
    assert record is not None, "the FAILED record must survive the request-level rollback"
    assert record.status == ProcessingStatus.FAILED
    assert record.error_message == "simulated failure"

    db.close()
    engine.dispose()
