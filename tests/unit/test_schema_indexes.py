"""Regression tests for P1-7 (PHASE1_AUDIT.md F6 — two missing indexes on
columns in live query paths) and the P1-6 unique constraint backing the
hook race fix. These check the SQLAlchemy model metadata directly, so a
future accidental removal of `index=True` (or the unique constraint) fails
a fast unit test instead of only showing up as a slow query or a duplicate
row much later."""

from content_factory.db.models.campaign import Campaign
from content_factory.db.models.hook import HookLibrary
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
