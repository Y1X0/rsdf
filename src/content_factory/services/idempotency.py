"""Generic idempotency protection for workflow-triggering actions
(adjustment #5: "the same campaign, script, or render request should never
create duplicate work"), implemented once and reused by every API endpoint
that creates a campaign or triggers agent/production work — rather than a
bespoke unique-constraint hack per table.

Usage pattern (see api/routers/*.py): the caller computes a `payload` dict
of the request's business-relevant fields, and either supplies an explicit
`idempotency_key` (client-controlled) or lets the request's own fingerprint
serve as the key — so identical repeated requests are deduplicated even
when the caller didn't think to pass a key.

**v1.1 fixes (PHASE1_AUDIT.md F1 and F5):**

1. The COMPLETED/FAILED transitions now `commit()` instead of `flush()`.
   Previously, a failed request's IdempotencyRecord was only ever flushed,
   so `api/deps.py`'s end-of-request rollback erased it along with
   everything else — meaning the "FAILED -> allow retry" branch below was
   reachable in unit tests (which call this function against a bare,
   never-rolled-back session) but not through the real API, where a failed
   request left *no* record behind at all. Committing here makes a FAILED
   record durably outlive the request that produced it, so a genuine retry
   finds it and resumes correctly instead of quietly starting over blind.
2. Creating a brand-new record is now a safe upsert (`_get_or_create_record`
   below): a concurrent duplicate request racing to create the same
   (scope, key) row no longer surfaces as an unhandled `IntegrityError` —
   the loser falls back to the winner's row instead.
"""

import hashlib
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from content_factory.db.models.enums import ProcessingStatus
from content_factory.db.models.idempotency import IdempotencyRecord
from content_factory.logging_config import get_logger

logger = get_logger(__name__)


class IdempotencyConflict(Exception):
    """The same idempotency key was reused with different request parameters."""


class IdempotencyInProgress(Exception):
    """A request with this key is currently being processed (rare race, not
    expected under Phase 1's synchronous request/response model, but
    guarded against regardless)."""


def compute_fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _query_record(db: Session, *, scope: str, key: str) -> IdempotencyRecord | None:
    return (
        db.query(IdempotencyRecord)
        .filter(IdempotencyRecord.scope == scope, IdempotencyRecord.key == key)
        .one_or_none()
    )


def _get_or_create_record(
    db: Session, *, scope: str, key: str, fingerprint: str
) -> tuple[IdempotencyRecord, bool]:
    """Safe upsert (PHASE1_AUDIT.md F5): try to insert a brand-new
    IN_PROGRESS record inside a SAVEPOINT; if a concurrent request already
    won the race to create the same (scope, key) row (unique constraint),
    roll back just the failed insert and fall back to the winner's row
    instead of surfacing the IntegrityError to the caller. Returns
    (record, just_created)."""
    existing = _query_record(db, scope=scope, key=key)
    if existing is not None:
        return existing, False

    try:
        with db.begin_nested():
            record = IdempotencyRecord(
                scope=scope, key=key, request_fingerprint=fingerprint, status=ProcessingStatus.IN_PROGRESS
            )
            db.add(record)
            db.flush()
        return record, True
    except IntegrityError:
        existing = _query_record(db, scope=scope, key=key)
        if existing is None:
            raise  # pragma: no cover - would mean the row vanished entirely, not a race
        return existing, False


def run_idempotent(
    db: Session,
    *,
    scope: str,
    idempotency_key: str | None,
    payload: dict,
    entity_type: str,
    work_fn: Callable[[], Any],
    load_existing: Callable[[int], Any],
) -> tuple[Any, bool]:
    """Returns (entity, created). `created` is False when an existing
    completed request was replayed instead of redoing the work."""
    fingerprint = compute_fingerprint(payload)
    key = idempotency_key or fingerprint

    record, just_created = _get_or_create_record(db, scope=scope, key=key, fingerprint=fingerprint)

    if not just_created:
        if record.request_fingerprint != fingerprint:
            raise IdempotencyConflict(
                f"Idempotency key {key!r} was already used for scope {scope!r} "
                "with different request parameters."
            )
        if record.status == ProcessingStatus.COMPLETED:
            logger.info("idempotent_replay", scope=scope, key=key, entity_id=record.result_entity_id)
            return load_existing(record.result_entity_id), False
        if record.status == ProcessingStatus.IN_PROGRESS:
            raise IdempotencyInProgress(f"Request {key!r} for scope {scope!r} is already in progress.")
        # FAILED -> allow retry, reuse the same record. This branch is now
        # reachable through the real API (not just direct unit tests)
        # because the FAILED transition below commits.
        record.status = ProcessingStatus.IN_PROGRESS
        record.error_message = None
        db.flush()

    try:
        entity = work_fn()
        db.flush()
        record.status = ProcessingStatus.COMPLETED
        record.result_entity_type = entity_type
        record.result_entity_id = entity.id
        db.commit()
        logger.info("idempotent_work_completed", scope=scope, key=key, entity_id=entity.id)
        return entity, True
    except Exception as exc:
        record.status = ProcessingStatus.FAILED
        record.error_message = str(exc)
        db.commit()
        logger.error("idempotent_work_failed", scope=scope, key=key, error=str(exc))
        raise
