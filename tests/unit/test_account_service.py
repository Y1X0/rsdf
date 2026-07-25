"""Phase 2 M3: account health scoring and warmup graduation."""

from datetime import UTC, datetime, timedelta

import pytest

from content_factory.config import Settings
from content_factory.db.models.account import OwnedAccount
from content_factory.db.models.enums import AccountHealthTier, AccountPlatform, AccountWarmupStatus
from content_factory.services import account_service
from content_factory.services.account_service import WarmupGraduationNotEligible


def test_compute_health_is_perfect_with_no_negative_signals():
    score, tier, cap_utilization_pct = account_service.compute_health(
        daily_post_cap=5, posting_cadence_used=2, engagement_trend=0.1, strikes_count=0, api_error_rate=0.0
    )
    assert score == 100.0
    assert tier == AccountHealthTier.HEALTHY
    assert cap_utilization_pct == pytest.approx(0.4)


def test_compute_health_penalizes_posting_beyond_cap():
    score, tier, cap_utilization_pct = account_service.compute_health(
        daily_post_cap=2, posting_cadence_used=6, engagement_trend=0.0, strikes_count=0, api_error_rate=0.0
    )
    assert cap_utilization_pct == pytest.approx(3.0)
    assert score < 100.0


def test_compute_health_drops_to_restricted_with_multiple_strikes():
    score, tier, _ = account_service.compute_health(
        daily_post_cap=5, posting_cadence_used=1, engagement_trend=0.0, strikes_count=5, api_error_rate=0.0
    )
    assert score == 0.0
    assert tier == AccountHealthTier.RESTRICTED


def test_compute_health_tier_boundaries():
    _, tier_watch, _ = account_service.compute_health(
        daily_post_cap=5, posting_cadence_used=1, engagement_trend=-0.6, strikes_count=0, api_error_rate=0.0
    )
    assert tier_watch == AccountHealthTier.WATCH

    _, tier_at_risk, _ = account_service.compute_health(
        daily_post_cap=5, posting_cadence_used=1, engagement_trend=0.0, strikes_count=3, api_error_rate=0.0
    )
    assert tier_at_risk == AccountHealthTier.AT_RISK


def test_record_health_check_persists_snapshot_and_updates_account(db_session):
    account = OwnedAccount(platform=AccountPlatform.TIKTOK, handle="creator1", daily_post_cap=3)
    db_session.add(account)
    db_session.flush()

    snapshot = account_service.record_health_check(
        db_session,
        account=account,
        posting_cadence_used=1,
        engagement_trend=0.05,
        strikes_count=0,
        api_error_rate=0.0,
    )

    assert snapshot.account_id == account.id
    assert account.health_score == snapshot.health_score
    assert account.health_tier == snapshot.tier


def test_warmup_graduation_blocked_when_account_too_new(db_session):
    account = OwnedAccount(platform=AccountPlatform.TIKTOK, handle="creator2", daily_post_cap=1)
    db_session.add(account)
    db_session.flush()
    settings = Settings(account_warmup_minimum_age_days=7)

    with pytest.raises(WarmupGraduationNotEligible):
        account_service.check_warmup_graduation_eligible(account, settings)


def test_warmup_graduation_blocked_when_health_tier_is_at_risk(db_session):
    account = OwnedAccount(platform=AccountPlatform.TIKTOK, handle="creator3", daily_post_cap=1)
    db_session.add(account)
    db_session.flush()
    account.created_at = datetime.now(UTC) - timedelta(days=30)
    account.health_tier = AccountHealthTier.AT_RISK
    settings = Settings(account_warmup_minimum_age_days=7)

    with pytest.raises(WarmupGraduationNotEligible):
        account_service.check_warmup_graduation_eligible(account, settings)


def test_warmup_graduation_allowed_when_criteria_met(db_session):
    account = OwnedAccount(platform=AccountPlatform.TIKTOK, handle="creator4", daily_post_cap=1)
    db_session.add(account)
    db_session.flush()
    account.created_at = datetime.now(UTC) - timedelta(days=30)
    account.warmup_status = AccountWarmupStatus.WARMING
    account.health_tier = AccountHealthTier.HEALTHY
    settings = Settings(account_warmup_minimum_age_days=7)

    account_service.check_warmup_graduation_eligible(account, settings)  # does not raise
