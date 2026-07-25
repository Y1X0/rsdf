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
"""

import hashlib
import json
from collections.abc import Callable
from typing import Any

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

    record = (
        db.query(IdempotencyRecord)
        .filter(IdempotencyRecord.scope == scope, IdempotencyRecord.key == key)
        .one_or_none()
    )

    if record is not None:
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
        # FAILED -> allow retry, reuse the same record.
        record.status = ProcessingStatus.IN_PROGRESS
        record.error_message = None
    else:
        record = IdempotencyRecord(
            scope=scope, key=key, request_fingerprint=fingerprint, status=ProcessingStatus.IN_PROGRESS
        )
        db.add(record)
    db.flush()

    try:
        entity = work_fn()
        db.flush()
        record.status = ProcessingStatus.COMPLETED
        record.result_entity_type = entity_type
        record.result_entity_id = entity.id
        db.flush()
        logger.info("idempotent_work_completed", scope=scope, key=key, entity_id=entity.id)
        return entity, True
    except Exception as exc:
        record.status = ProcessingStatus.FAILED
        record.error_message = str(exc)
        db.flush()
        logger.error("idempotent_work_failed", scope=scope, key=key, error=str(exc))
        raise
