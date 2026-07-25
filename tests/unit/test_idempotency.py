import pytest

from content_factory.services.idempotency import (
    IdempotencyConflict,
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
