"""Regression test for a real production bug found via a live Render
traceback (GET /source-videos): migration 0014 added `source_videos.source`
with `server_default='upload'` (lowercase - the enum member's *value*),
but SQLAlchemy's Enum column type (native_enum=False) reads/writes using
the member's *name* ("UPLOAD"/"CONTENT_REWARDS") by default - so every
row that existed at migration-0014 time was backfilled with the broken
lowercase string, which the ORM cannot map back to a SourceVideoOrigin
member on read:

    LookupError: 'upload' is not among the defined enum values.
    Enum name: sourcevideoorigin. Possible values: UPLOAD, CONTENT_REWARDS.

alembic/versions/0016_fix_source_video_origin_enum_case.py normalizes
existing rows. This test proves the exact SQL that migration issues
actually fixes a row simulating the broken pre-migration state, and that
the fixed row round-trips cleanly through both the ORM and the API's own
response schema (SourceVideoOut) - the two things GET /source-videos
depends on. CI's own "apply migrations" / "migration reversibility"
steps (against real Postgres) separately exercise the migration file
itself end-to-end; this test targets the underlying data-transformation
logic against this suite's SQLite test database.
"""

import pytest
from sqlalchemy import text

from content_factory.db.models.enums import SourceVideoOrigin
from content_factory.db.models.source_video import SourceVideo
from content_factory.schemas.source_video import SourceVideoOut


def _make_source_video(db_session) -> SourceVideo:
    sv = SourceVideo(title="Test Video", storage_path="/tmp/does-not-need-to-exist.mp4")
    db_session.add(sv)
    db_session.flush()
    return sv


def test_a_row_with_the_broken_lowercase_value_fails_to_read_before_the_fix(db_session):
    """Documents the exact real bug: a row whose `source` column holds the
    broken lowercase 'upload' (as migration 0014's server_default wrote
    for every pre-existing row) cannot be read back via the ORM at all -
    this is precisely what crashed GET /source-videos in production."""
    sv = _make_source_video(db_session)
    sv_id = sv.id
    db_session.execute(text("UPDATE source_videos SET source = 'upload' WHERE id = :id"), {"id": sv_id})
    db_session.commit()
    db_session.expire_all()

    with pytest.raises(LookupError):
        db_session.get(SourceVideo, sv_id).source


def test_normalizing_the_value_the_way_migration_0016_does_fixes_the_read(db_session):
    """Applies the exact same UPDATE statements
    alembic/versions/0016_fix_source_video_origin_enum_case.py::upgrade()
    issues, directly against a row simulating the broken pre-migration
    state, and confirms it now reads cleanly as a real SourceVideoOrigin
    member - and that the API's own response schema
    (SourceVideoOut, used by GET /source-videos) can serialize it, since
    that's the exact code path that crashed in production."""
    sv = _make_source_video(db_session)
    sv_id = sv.id
    db_session.execute(text("UPDATE source_videos SET source = 'upload' WHERE id = :id"), {"id": sv_id})
    db_session.commit()
    db_session.expire_all()

    # Same normalization migration 0016's upgrade() runs.
    db_session.execute(text("UPDATE source_videos SET source = 'UPLOAD' WHERE source = 'upload'"))
    db_session.execute(
        text("UPDATE source_videos SET source = 'CONTENT_REWARDS' WHERE source = 'content_rewards'")
    )
    db_session.commit()
    db_session.expire_all()

    fixed = db_session.get(SourceVideo, sv_id)
    assert fixed.source == SourceVideoOrigin.UPLOAD

    # The exact call GET /source-videos makes for every row.
    out = SourceVideoOut.model_validate(fixed)
    assert out.source == SourceVideoOrigin.UPLOAD


def test_normalization_is_a_noop_for_rows_already_stored_correctly(db_session):
    """Rows created through the ORM after migration 0014 already store the
    correct uppercase value - the normalization UPDATE must never touch
    them (its WHERE clause only matches the broken lowercase strings)."""
    sv = SourceVideo(
        title="Already Correct", storage_path="/tmp/x.mp4", source=SourceVideoOrigin.CONTENT_REWARDS
    )
    db_session.add(sv)
    db_session.flush()
    sv_id = sv.id
    db_session.commit()

    db_session.execute(text("UPDATE source_videos SET source = 'UPLOAD' WHERE source = 'upload'"))
    db_session.execute(
        text("UPDATE source_videos SET source = 'CONTENT_REWARDS' WHERE source = 'content_rewards'")
    )
    db_session.commit()
    db_session.expire_all()

    assert db_session.get(SourceVideo, sv_id).source == SourceVideoOrigin.CONTENT_REWARDS
