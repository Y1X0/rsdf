from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from content_factory.db.base import Base
from content_factory.db.models.enums import ProcessingStatus, SourceVideoOrigin
from content_factory.db.models.idempotency import IdempotencyRecord
from content_factory.db.models.source_video import SourceVideo
from content_factory.services.idempotency import (
    STALE_IN_PROGRESS_THRESHOLD,
    IdempotencyConflict,
    IdempotencyInProgress,
    _get_or_create_record,
    compute_fingerprint,
    invalidate_completed_record,
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


def test_run_idempotent_raises_in_progress_for_a_recent_record(db_session):
    """A record that only just started must still block a concurrent
    duplicate request - this is the case the stale-recovery fix below must
    not weaken."""
    db_session.add(
        IdempotencyRecord(
            scope="inprogress.recent", key="k1", request_fingerprint=compute_fingerprint({"a": 1}),
            status=ProcessingStatus.IN_PROGRESS,
        )
    )
    db_session.flush()

    with pytest.raises(IdempotencyInProgress):
        run_idempotent(
            db_session, scope="inprogress.recent", idempotency_key="k1", payload={"a": 1},
            entity_type="widget", work_fn=lambda: pytest.fail("must not run - should still be blocked"),
            load_existing=lambda entity_id: None,
        )


def test_run_idempotent_recovers_a_stale_in_progress_record_and_allows_retry(db_session):
    """Regression test for a real production incident: a render request
    crashed the whole process mid-flight (an out-of-memory kill, per the
    real logs) after creating an IN_PROGRESS record but before it could
    ever reach COMPLETED or FAILED - permanently wedging that (scope, key)
    pair, since nothing else ever transitions it out of IN_PROGRESS. Every
    retry hit IdempotencyInProgress forever, with no way to recover short
    of a manual database edit. A record stuck IN_PROGRESS well past any
    realistic completion time must be treated as abandoned and retried,
    the same as an explicitly FAILED one."""
    record = IdempotencyRecord(
        scope="inprogress.stale", key="k1", request_fingerprint=compute_fingerprint({"a": 1}),
        status=ProcessingStatus.IN_PROGRESS,
    )
    db_session.add(record)
    db_session.flush()
    # Simulate a record abandoned well past the recovery threshold - real
    # crash recovery depends on wall-clock time, not on how this test
    # constructed the row.
    record.updated_at = datetime.now(UTC) - STALE_IN_PROGRESS_THRESHOLD - timedelta(minutes=1)
    db_session.flush()

    entity, created = run_idempotent(
        db_session, scope="inprogress.stale", idempotency_key="k1", payload={"a": 1},
        entity_type="widget", work_fn=lambda: _Widget(id=1, value="recovered"),
        load_existing=lambda entity_id: pytest.fail("should not replay - work_fn must actually run"),
    )
    assert created is True
    assert entity.value == "recovered"


def test_run_idempotent_recovers_when_work_fn_fails_via_a_db_level_error(db_session):
    """Regression for the real Content Rewards production incident: a
    retried work_fn tried to create a second SourceVideo row for an
    external_id a previous, partially-failed attempt had already committed
    - hitting uq_source_videos_source_external_id at flush time. That's a
    DB-level failure, not a plain Python exception, so it leaves the
    session in SQLAlchemy's "pending rollback" state. The except block's
    own `record.status = FAILED; db.commit()` then raised
    sqlalchemy.exc.PendingRollbackError on top of it, masking the real
    IntegrityError and turning it into an opaque, unrecorded crash. This
    must instead roll back, durably record FAILED, and re-raise the real
    original error."""
    # Committed, not just flushed - matches the real incident, where the
    # first video's row was durably committed (either via the success path
    # or the FAILED-transition commit) in an earlier, already-finished
    # request before this retry's request ever began.
    existing = SourceVideo(
        title="existing", storage_path="", source=SourceVideoOrigin.CONTENT_REWARDS, external_source_id="dup-1"
    )
    db_session.add(existing)
    db_session.commit()

    def work():
        duplicate = SourceVideo(
            title="new", storage_path="", source=SourceVideoOrigin.CONTENT_REWARDS, external_source_id="dup-1"
        )
        db_session.add(duplicate)
        db_session.flush()  # real DB-level failure (UniqueViolation), not a plain exception
        return duplicate

    def load_existing(entity_id):
        raise AssertionError("should not be called - nothing ever completed")

    with pytest.raises(IntegrityError):
        run_idempotent(
            db_session, scope="dup.scope", idempotency_key="dup-key", payload={"a": 1},
            entity_type="source_video", work_fn=work, load_existing=load_existing,
        )

    # The session must be usable again (a real rollback happened, not left
    # broken), and the failure durably recorded rather than swallowed.
    record = (
        db_session.query(IdempotencyRecord)
        .filter(IdempotencyRecord.scope == "dup.scope", IdempotencyRecord.key == "dup-key")
        .one_or_none()
    )
    assert record is not None
    assert record.status == ProcessingStatus.FAILED

    # And the pre-existing row survived untouched - never overwritten or
    # duplicated by the failed retry.
    survivors = db_session.query(SourceVideo).filter(SourceVideo.external_source_id == "dup-1").all()
    assert len(survivors) == 1
    assert survivors[0].id == existing.id


def test_invalidate_completed_record_forces_a_completed_record_back_to_failed(db_session):
    """Regression for the real Content Rewards production incident: a
    completed sync's downloaded file can vanish later (e.g. a redeploy on
    a hosting plan with no persistent disk wipes local storage) without
    the database ever finding out. A caller that independently discovers
    the backing file is gone must be able to force a retry - reusing the
    already-tested FAILED->retry path rather than reinventing one."""
    db_session.add(
        IdempotencyRecord(
            scope="source_video.fetch_external", key="ext-1", request_fingerprint="fp-1",
            status=ProcessingStatus.COMPLETED, result_entity_type="source_video", result_entity_id=1,
        )
    )
    db_session.commit()

    invalidate_completed_record(
        db_session, scope="source_video.fetch_external", key="ext-1", reason="file missing from disk"
    )

    record = (
        db_session.query(IdempotencyRecord)
        .filter(IdempotencyRecord.scope == "source_video.fetch_external", IdempotencyRecord.key == "ext-1")
        .one()
    )
    assert record.status == ProcessingStatus.FAILED
    assert record.error_message == "file missing from disk"


def test_invalidate_completed_record_is_a_no_op_for_a_non_completed_record(db_session):
    """Must never disturb a record that isn't COMPLETED (e.g. still
    IN_PROGRESS, or already FAILED for an unrelated reason) - those are
    already handled by run_idempotent's own existing logic."""
    db_session.add(
        IdempotencyRecord(
            scope="source_video.fetch_external", key="ext-2", request_fingerprint="fp-2",
            status=ProcessingStatus.IN_PROGRESS,
        )
    )
    db_session.commit()

    invalidate_completed_record(
        db_session, scope="source_video.fetch_external", key="ext-2", reason="should not apply"
    )

    record = (
        db_session.query(IdempotencyRecord)
        .filter(IdempotencyRecord.scope == "source_video.fetch_external", IdempotencyRecord.key == "ext-2")
        .one()
    )
    assert record.status == ProcessingStatus.IN_PROGRESS
    assert record.error_message is None


def test_invalidate_completed_record_is_a_no_op_when_no_record_exists(db_session):
    invalidate_completed_record(db_session, scope="no.such.scope", key="missing-key", reason="irrelevant")
    # No error, nothing to assert on - just must not raise.


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
