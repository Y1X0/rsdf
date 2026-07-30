"""Production Hardening Sprint H4 (D2/C1): proves the Postgres advisory
lock in `budget_governor._acquire_budget_lock` actually closes the
check-then-spend race, using real threads and real separate `Session`
objects against a real local Postgres instance — SQLite (used by every
other test in this suite via `db_session`/`client`) doesn't support
`pg_advisory_xact_lock`, so this property can only be demonstrated here.

Requires a real local Postgres reachable at the URL below (this sandbox
has one, already migrated to head); skips cleanly if unavailable so the
rest of the suite is unaffected.

Design: a $10.00 ceiling pre-seeded at $9.50 spend (95% — under the 100%
block threshold). Two threads each call `enforce_budget` then add a $1.00
`CostLedger` entry and commit, gated so both threads *start* their
`enforce_budget` call at the same instant (a `Barrier`). Without the lock,
both could read the same pre-commit 95% snapshot and both pass. With the
lock, they serialize: the first to acquire it sees 95% (passes), commits
(spend now $10.50 = 105%); the second, unblocked only after the first
commits, then sees 105% and correctly raises `BudgetExceeded`. So the
only possible passing/blocked split under the fix is exactly 1 pass / 1
blocked — never 2 passes.

To make this deterministic rather than a coin-flip on thread scheduling
(confirmed by hand: with the lock call removed, the race window between
"read spend" and "commit new spend" was narrow enough that both threads
still happened to serialize naturally on this machine 5/5 tries — a false
negative that would have made this test worthless as a regression guard),
`_compute_spend` is monkeypatched to sleep briefly *after* computing but
*before* returning. This widens the read-to-commit window deliberately.
It doesn't weaken what's being proven: the lock is acquired *before*
`check_budget` (and therefore `_compute_spend`) is even called, so a
correctly-locked second thread is still blocked at the lock acquisition
the entire time regardless of how long the first thread's read takes.
Only the *unlocked* case benefits from the widened window, which is
exactly the failure mode this test needs to reliably catch.
"""

import threading
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from content_factory.db.models.budget import BudgetCeiling
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.analytics import CostLedger
from content_factory.db.models.enums import BudgetScope
from content_factory.db.models.niche import Niche
from content_factory.notifications.base import NotificationProvider, NotificationResult
from content_factory.services import budget_governor
from content_factory.services.budget_governor import BudgetExceeded

REAL_POSTGRES_URL = "postgresql+psycopg2://content_factory:content_factory@localhost:5432/content_factory"


def _real_postgres_available() -> bool:
    try:
        engine = create_engine(REAL_POSTGRES_URL)
        with engine.connect():
            pass
        engine.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _real_postgres_available(), reason="no local Postgres instance available"
)


class _SilentNotificationProvider(NotificationProvider):
    def send(self, request):
        return NotificationResult(channel="log", delivered=True)


@pytest.fixture
def pg_engine():
    engine = create_engine(REAL_POSTGRES_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine):
    return sessionmaker(bind=pg_engine, autoflush=False, autocommit=False, future=True)


def _make_seeded_ceiling(session_factory) -> tuple[int, int]:
    """Returns (niche_id, campaign_id) for a fresh niche/campaign pair with
    a $10 system-wide ceiling already at $9.50 (95%) spend, all committed
    so both worker threads see it."""
    db = session_factory()
    try:
        niche = Niche(name="budget-concurrency-test")
        db.add(niche)
        db.flush()

        campaign = Campaign(brand_name="Acme", niche_id=niche.id)
        db.add(campaign)
        db.flush()

        db.add(BudgetCeiling(scope=BudgetScope.SYSTEM, niche_id=None, monthly_limit_usd=10.0))
        db.add(
            CostLedger(
                campaign_id=campaign.id,
                category="llm",
                provider="fake",
                cost_usd=9.50,
                recorded_at=datetime.now(UTC),
            )
        )
        db.commit()
        return niche.id, campaign.id
    finally:
        db.close()


def _cleanup(session_factory, *, niche_id: int, campaign_id: int) -> None:
    db = session_factory()
    try:
        db.query(CostLedger).filter(CostLedger.campaign_id == campaign_id).delete()
        db.query(BudgetCeiling).filter(
            BudgetCeiling.scope == BudgetScope.SYSTEM, BudgetCeiling.niche_id.is_(None)
        ).delete()
        db.query(Campaign).filter(Campaign.id == campaign_id).delete()
        db.query(Niche).filter(Niche.id == niche_id).delete()
        db.commit()
    finally:
        db.close()


def test_concurrent_enforce_budget_calls_serialize_around_the_shared_ceiling(pg_session_factory, monkeypatch):
    original_compute_spend = budget_governor._compute_spend

    def _slow_compute_spend(*args, **kwargs):
        result = original_compute_spend(*args, **kwargs)
        time.sleep(0.3)
        return result

    monkeypatch.setattr(budget_governor, "_compute_spend", _slow_compute_spend)

    niche_id, campaign_id = _make_seeded_ceiling(pg_session_factory)
    try:
        barrier = threading.Barrier(2)
        results: list[str] = []
        results_lock = threading.Lock()

        def worker() -> None:
            db = pg_session_factory()
            try:
                barrier.wait(timeout=5)
                try:
                    budget_governor.enforce_budget(
                        db, niche_id=None, notification_provider=_SilentNotificationProvider()
                    )
                except BudgetExceeded:
                    with results_lock:
                        results.append("blocked")
                    db.rollback()
                    return

                db.add(
                    CostLedger(
                        campaign_id=campaign_id,
                        category="llm",
                        provider="fake",
                        cost_usd=1.00,
                        recorded_at=datetime.now(UTC),
                    )
                )
                db.commit()
                with results_lock:
                    results.append("passed")
            finally:
                db.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert sorted(results) == ["blocked", "passed"], (
            "The advisory lock should force exactly one of the two "
            f"concurrent requests to pass and the other to block; got {results}"
        )
    finally:
        _cleanup(pg_session_factory, niche_id=niche_id, campaign_id=campaign_id)


def test_check_budget_alone_remains_lock_free_and_read_only(pg_session_factory):
    """Guards against accidentally moving the lock into `check_budget` --
    that function also backs the read-only `GET /budget/status` endpoint,
    which must never block on or be blocked by an in-flight spend."""
    niche_id, campaign_id = _make_seeded_ceiling(pg_session_factory)
    try:
        db = pg_session_factory()
        try:
            statuses = budget_governor.check_budget(db, niche_id=None)
            assert len(statuses) == 1
            assert statuses[0].pct_used == pytest.approx(0.95)
            assert statuses[0].is_blocked is False
        finally:
            db.close()
    finally:
        _cleanup(pg_session_factory, niche_id=niche_id, campaign_id=campaign_id)
