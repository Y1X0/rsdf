"""Creator Account Management (ARCHITECTURE.md §8) — Phase 2 M3.

Health scoring is a heuristic composite, matching quality_scoring.py's
Phase 1/2 approach: real signals combined with hand-picked weights, not a
learned model (retention_prediction_score-style upgrades are a later
phase). There is no automated posting-cadence data source yet — that's
Publishing Agent/M4's `publications` table — so `daily_post_cap` vs.
posting cadence is supplied by the caller (an operator or an external
cron), the same "manual entry until real automation exists" pattern
Phase 1's `POST /videos/{id}/metrics` already established.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from content_factory.config import Settings
from content_factory.db.models.account import AccountHealthSnapshot, OwnedAccount
from content_factory.db.models.enums import AccountHealthTier, AccountWarmupStatus
from content_factory.logging_config import get_logger

logger = get_logger(__name__)

# Ordered highest-threshold-first: the first tier whose threshold the score
# meets or exceeds wins. Anything below the lowest threshold is Restricted.
_TIER_THRESHOLDS: tuple[tuple[float, AccountHealthTier], ...] = (
    (80.0, AccountHealthTier.HEALTHY),
    (50.0, AccountHealthTier.WATCH),
    (20.0, AccountHealthTier.AT_RISK),
)


class WarmupGraduationNotEligible(Exception):
    pass


def compute_health(
    *,
    daily_post_cap: int,
    posting_cadence_used: int,
    engagement_trend: float,
    strikes_count: int,
    api_error_rate: float,
) -> tuple[float, AccountHealthTier, float]:
    """Returns (health_score 0..100, tier, cap_utilization_pct).

    Posting beyond the account's own cap, a negative engagement trend
    (against the account's own baseline, not a global one — ARCHITECTURE.md
    §8b), platform strikes, and API error rate (an early restriction
    signal) all pull the score down independently, then the score is
    clamped and mapped to a tier.
    """
    cap_utilization_pct = posting_cadence_used / daily_post_cap if daily_post_cap > 0 else 0.0

    score = 100.0
    if cap_utilization_pct > 1.0:
        score -= min((cap_utilization_pct - 1.0) * 100.0, 40.0)
    if engagement_trend < 0:
        score -= min(-engagement_trend * 50.0, 50.0)
    score -= min(strikes_count * 20.0, 100.0)
    score -= min(api_error_rate * 100.0, 100.0)
    score = max(0.0, min(100.0, score))

    tier = AccountHealthTier.RESTRICTED
    for threshold, candidate_tier in _TIER_THRESHOLDS:
        if score >= threshold:
            tier = candidate_tier
            break

    return round(score, 2), tier, round(cap_utilization_pct, 4)


def record_health_check(
    db: Session,
    *,
    account: OwnedAccount,
    posting_cadence_used: int,
    engagement_trend: float,
    strikes_count: int,
    api_error_rate: float,
) -> AccountHealthSnapshot:
    score, tier, cap_utilization_pct = compute_health(
        daily_post_cap=account.daily_post_cap,
        posting_cadence_used=posting_cadence_used,
        engagement_trend=engagement_trend,
        strikes_count=strikes_count,
        api_error_rate=api_error_rate,
    )

    snapshot = AccountHealthSnapshot(
        account_id=account.id,
        captured_at=datetime.now(UTC),
        health_score=score,
        tier=tier,
        posting_cadence_used=posting_cadence_used,
        cap_utilization_pct=cap_utilization_pct,
        engagement_trend=engagement_trend,
        strikes_count=strikes_count,
        api_error_rate=api_error_rate,
    )
    db.add(snapshot)

    account.health_score = score
    account.health_tier = tier
    db.flush()

    logger.info(
        "account_health_check_completed",
        account_id=account.id,
        health_score=score,
        tier=tier.value,
        cap_utilization_pct=cap_utilization_pct,
    )
    return snapshot


def check_warmup_graduation_eligible(account: OwnedAccount, settings: Settings) -> None:
    """Raises WarmupGraduationNotEligible if `account` cannot yet move from
    warming to active. Criteria implemented: minimum account age, no
    active policy flags (proxied by health tier — see module docstring for
    the "minimum organic post count" criterion this doesn't yet check)."""
    if account.warmup_status != AccountWarmupStatus.WARMING:
        return

    # SQLite (the default DATABASE_URL backend) doesn't preserve tzinfo
    # across a round trip even for a DateTime(timezone=True) column, so a
    # freshly-loaded created_at can come back naive — normalize before
    # subtracting rather than assuming the backend always keeps it aware.
    created_at = account.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    account_age_days = (datetime.now(UTC) - created_at).days
    if account_age_days < settings.account_warmup_minimum_age_days:
        raise WarmupGraduationNotEligible(
            f"Account is {account_age_days} day(s) old; "
            f"minimum warmup age is {settings.account_warmup_minimum_age_days} day(s)."
        )

    if account.health_tier in (AccountHealthTier.AT_RISK, AccountHealthTier.RESTRICTED):
        raise WarmupGraduationNotEligible(f"Account health tier is {account.health_tier.value}; cannot graduate.")
