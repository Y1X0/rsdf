"""Regression tests for P1-7 (PHASE1_AUDIT.md F6 — two missing indexes on
columns in live query paths), the P1-6 unique constraint backing the hook
race fix, and Production Hardening Sprint H5 (the production readiness
review's DB1 finding — missing hot-path indexes, and redundant indexes
superseded by an existing composite unique constraint). These check the
SQLAlchemy model metadata directly, so a future accidental removal of
`index=True` (or a constraint/composite index) fails a fast unit test
instead of only showing up as a slow query or a duplicate row much later.
See alembic/versions/0008_database_optimization.py for the migration and
docs/DEPLOYMENT.md/PRODUCTION_HARDENING_REPORT.md for the reasoning."""

from content_factory.db.models.analytics import CostLedger
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.experiment import ExperimentResult
from content_factory.db.models.hook import HookLibrary
from content_factory.db.models.idempotency import IdempotencyRecord
from content_factory.db.models.publication import Publication
from content_factory.db.models.video import Video


def test_video_status_column_is_indexed():
    assert Video.__table__.columns["status"].index is True


def test_campaign_niche_id_column_is_indexed():
    assert Campaign.__table__.columns["niche_id"].index is True


def test_hook_library_has_unique_constraint_on_niche_and_text():
    constraint_columns = {
        tuple(col.name for col in constraint.columns)
        for constraint in HookLibrary.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("niche_id", "hook_text") in constraint_columns


def test_cost_ledger_recorded_at_is_indexed():
    """H5: filtered with `>=` on every enforce_budget call
    (budget_governor._compute_spend) — a hot path with no index at all
    before this sprint."""
    assert CostLedger.__table__.columns["recorded_at"].index is True


def test_experiment_results_is_winner_is_indexed():
    """H5: filtered by the default (winners_only=True)
    GET /experimentation/recommendations path."""
    assert ExperimentResult.__table__.columns["is_winner"].index is True


def test_publications_has_composite_account_status_published_index():
    """H5: replaces three separate single-column indexes (account_id,
    status; published_at was never indexed) with one composite index
    matching publishing_service.py's actual cadence-cap query shape
    (account_id + status + published_at >= start_of_day, together)."""
    index_columns = {
        tuple(col.name for col in index.columns) for index in Publication.__table__.indexes
    }
    assert ("account_id", "status", "published_at") in index_columns
    # The old standalone indexes are gone, not just superseded by the
    # composite existing alongside them.
    assert Publication.__table__.columns["account_id"].index is not True
    assert Publication.__table__.columns["status"].index is not True


def test_idempotency_records_scope_and_key_have_no_redundant_standalone_indexes():
    """H5: every query filters on (scope, key) together
    (services/idempotency.py), never on either column alone, and the
    existing UniqueConstraint("scope", "key") already provides a composite
    index that covers that — standalone single-column indexes here were
    dead weight."""
    assert IdempotencyRecord.__table__.columns["scope"].index is not True
    assert IdempotencyRecord.__table__.columns["key"].index is not True
    constraint_columns = {
        tuple(col.name for col in constraint.columns)
        for constraint in IdempotencyRecord.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("scope", "key") in constraint_columns
