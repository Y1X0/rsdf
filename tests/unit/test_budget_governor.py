"""Tests for Phase 2 M1's active Cost Control Layer (ARCHITECTURE.md §10c).

Covers: the governor blocks at/over 100% of a ceiling (system-wide and
niche-scoped), alerts fire exactly once per newly-crossed threshold (not on
every subsequent check), and a niche-scoped ceiling doesn't interfere with
an unrelated niche's spend.
"""

from datetime import UTC, datetime

import pytest

from content_factory.db.models.budget import BudgetCeiling
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.analytics import CostLedger
from content_factory.db.models.enums import BudgetScope
from content_factory.db.models.niche import Niche
from content_factory.notifications.base import NotificationProvider, NotificationResult
from content_factory.services import budget_governor
from content_factory.services.budget_governor import BudgetExceeded


class _RecordingNotificationProvider(NotificationProvider):
    def __init__(self):
        self.sent = []

    def send(self, request):
        self.sent.append(request)
        return NotificationResult(channel="log", delivered=True)


def _make_niche(db, name="budget-test-niche") -> Niche:
    niche = Niche(name=name)
    db.add(niche)
    db.flush()
    return niche


def _make_campaign(db, niche_id) -> Campaign:
    campaign = Campaign(brand_name="Acme", niche_id=niche_id)
    db.add(campaign)
    db.flush()
    return campaign


def _add_cost(db, *, campaign_id, cost_usd) -> None:
    db.add(
        CostLedger(
            campaign_id=campaign_id,
            category="llm",
            provider="fake",
            cost_usd=cost_usd,
            recorded_at=datetime.now(UTC),
        )
    )
    db.flush()


def test_check_budget_returns_no_statuses_when_no_ceiling_configured(db_session):
    statuses = budget_governor.check_budget(db_session, niche_id=None)
    assert statuses == []


def test_check_budget_computes_pct_used_from_cost_ledger(db_session):
    db_session.add(BudgetCeiling(scope=BudgetScope.SYSTEM, niche_id=None, monthly_limit_usd=100.0))
    db_session.flush()
    niche = _make_niche(db_session)
    campaign = _make_campaign(db_session, niche.id)
    _add_cost(db_session, campaign_id=campaign.id, cost_usd=40.0)

    statuses = budget_governor.check_budget(db_session, niche_id=None)
    assert len(statuses) == 1
    assert statuses[0].spend_usd == 40.0
    assert statuses[0].pct_used == pytest.approx(0.4)
    assert statuses[0].is_blocked is False


def test_enforce_budget_raises_when_system_ceiling_is_met(db_session):
    db_session.add(BudgetCeiling(scope=BudgetScope.SYSTEM, niche_id=None, monthly_limit_usd=50.0))
    db_session.flush()
    niche = _make_niche(db_session)
    campaign = _make_campaign(db_session, niche.id)
    _add_cost(db_session, campaign_id=campaign.id, cost_usd=50.0)

    notification_provider = _RecordingNotificationProvider()
    with pytest.raises(BudgetExceeded):
        budget_governor.enforce_budget(db_session, niche_id=None, notification_provider=notification_provider)


def test_enforce_budget_does_not_raise_below_ceiling(db_session):
    db_session.add(BudgetCeiling(scope=BudgetScope.SYSTEM, niche_id=None, monthly_limit_usd=100.0))
    db_session.flush()
    niche = _make_niche(db_session)
    campaign = _make_campaign(db_session, niche.id)
    _add_cost(db_session, campaign_id=campaign.id, cost_usd=10.0)

    notification_provider = _RecordingNotificationProvider()
    statuses = budget_governor.enforce_budget(db_session, niche_id=None, notification_provider=notification_provider)
    assert len(statuses) == 1
    assert statuses[0].is_blocked is False


def test_niche_ceiling_blocks_independently_of_system_ceiling(db_session):
    db_session.add(BudgetCeiling(scope=BudgetScope.SYSTEM, niche_id=None, monthly_limit_usd=1000.0))
    niche = _make_niche(db_session)
    db_session.add(BudgetCeiling(scope=BudgetScope.NICHE, niche_id=niche.id, monthly_limit_usd=20.0))
    db_session.flush()
    campaign = _make_campaign(db_session, niche.id)
    _add_cost(db_session, campaign_id=campaign.id, cost_usd=25.0)

    notification_provider = _RecordingNotificationProvider()
    with pytest.raises(BudgetExceeded) as exc_info:
        budget_governor.enforce_budget(db_session, niche_id=niche.id, notification_provider=notification_provider)
    assert exc_info.value.status.ceiling.scope == BudgetScope.NICHE


def test_unrelated_niche_spend_does_not_affect_another_niches_ceiling(db_session):
    niche_a = _make_niche(db_session, name="niche-a")
    niche_b = _make_niche(db_session, name="niche-b")
    db_session.add(BudgetCeiling(scope=BudgetScope.NICHE, niche_id=niche_a.id, monthly_limit_usd=10.0))
    db_session.flush()

    campaign_b = _make_campaign(db_session, niche_b.id)
    _add_cost(db_session, campaign_id=campaign_b.id, cost_usd=999.0)

    statuses = budget_governor.check_budget(db_session, niche_id=niche_a.id)
    assert len(statuses) == 1
    assert statuses[0].spend_usd == 0.0
    assert statuses[0].is_blocked is False


def test_alert_fires_exactly_once_per_threshold_crossing(db_session):
    ceiling = BudgetCeiling(scope=BudgetScope.SYSTEM, niche_id=None, monthly_limit_usd=100.0)
    db_session.add(ceiling)
    db_session.flush()
    niche = _make_niche(db_session)
    campaign = _make_campaign(db_session, niche.id)

    notification_provider = _RecordingNotificationProvider()

    # Cross 50% -> exactly one alert.
    _add_cost(db_session, campaign_id=campaign.id, cost_usd=55.0)
    budget_governor.enforce_budget(db_session, niche_id=None, notification_provider=notification_provider)
    assert len(notification_provider.sent) == 1
    assert ceiling.last_alert_threshold_pct == 0.5

    # Checking again with no new spend must not re-fire the 50% alert.
    budget_governor.enforce_budget(db_session, niche_id=None, notification_provider=notification_provider)
    assert len(notification_provider.sent) == 1

    # Crossing into 80% fires exactly one more alert (not two, even though
    # both 50% and 80% are technically "at or below" the new pct).
    _add_cost(db_session, campaign_id=campaign.id, cost_usd=26.0)
    budget_governor.enforce_budget(db_session, niche_id=None, notification_provider=notification_provider)
    assert len(notification_provider.sent) == 2
    assert ceiling.last_alert_threshold_pct == 0.8


def test_alert_fires_at_100_percent_alongside_the_block(db_session):
    ceiling = BudgetCeiling(scope=BudgetScope.SYSTEM, niche_id=None, monthly_limit_usd=100.0)
    db_session.add(ceiling)
    db_session.flush()
    niche = _make_niche(db_session)
    campaign = _make_campaign(db_session, niche.id)
    _add_cost(db_session, campaign_id=campaign.id, cost_usd=100.0)

    notification_provider = _RecordingNotificationProvider()
    with pytest.raises(BudgetExceeded):
        budget_governor.enforce_budget(db_session, niche_id=None, notification_provider=notification_provider)

    assert len(notification_provider.sent) == 1
    assert notification_provider.sent[0].severity.value == "critical"
    assert ceiling.last_alert_threshold_pct == 1.0
