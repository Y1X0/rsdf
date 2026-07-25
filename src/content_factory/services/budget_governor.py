"""Active Cost Control Layer (ARCHITECTURE.md §10c) — Phase 2 M1.

Distinct from Phase 1's passive `cost_ledger` (which only ever recorded
spend after the fact): this module actively *gates* new spend. Deliberately
**computed on demand** from `cost_ledger` rather than maintained as a
separate running counter — a derived value can't drift out of sync with
the ledger it's derived from, which is a stronger correctness property
than a cached counter that some code path might forget to update.

`enforce_budget` is called at the top of every cost-incurring endpoint
(research, script generation, render — see api/routers/content.py) and
raises `BudgetExceeded` the moment any applicable ceiling (system-wide,
and the specific niche's if one exists) is at or past 100% for the current
calendar month — fail-closed, per ARCHITECTURE.md §20's general principle
and §10c's "this is a fail-closed control" specifically. Crossing 50/80/95%
fires an alert via the injected NotificationProvider exactly once per
threshold, tracked on the ceiling row itself
(`BudgetCeiling.last_alert_threshold_pct`) so repeated calls within the
same month don't re-alert.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from content_factory.db.models.analytics import CostLedger
from content_factory.db.models.budget import BudgetCeiling
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.enums import BudgetScope, NotificationSeverity
from content_factory.db.models.notification import NotificationLog
from content_factory.logging_config import get_logger
from content_factory.notifications.base import NotificationProvider, NotificationRequest

logger = get_logger(__name__)

ALERT_THRESHOLDS = (0.5, 0.8, 0.95, 1.0)


class BudgetExceeded(Exception):
    def __init__(self, status: "BudgetStatus") -> None:
        self.status = status
        scope_desc = "system-wide" if status.ceiling.scope == BudgetScope.SYSTEM else f"niche {status.ceiling.niche_id}"
        super().__init__(
            f"Monthly budget ceiling exceeded ({scope_desc}): "
            f"${status.spend_usd:.2f} / ${status.ceiling.monthly_limit_usd:.2f} "
            f"({status.pct_used:.0%})"
        )


@dataclass(frozen=True)
class BudgetStatus:
    ceiling: BudgetCeiling
    spend_usd: float
    pct_used: float
    is_blocked: bool


def _current_month_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _latest_ceiling(db: Session, *, scope: BudgetScope, niche_id: int | None) -> BudgetCeiling | None:
    return (
        db.query(BudgetCeiling)
        .filter(BudgetCeiling.scope == scope, BudgetCeiling.niche_id == niche_id)
        .order_by(BudgetCeiling.created_at.desc())
        .first()
    )


def _compute_spend(db: Session, *, niche_id: int | None, period_start: datetime) -> float:
    query = db.query(func.coalesce(func.sum(CostLedger.cost_usd), 0)).filter(
        CostLedger.recorded_at >= period_start
    )
    if niche_id is not None:
        query = query.join(Campaign, CostLedger.campaign_id == Campaign.id).filter(
            Campaign.niche_id == niche_id
        )
    return float(query.scalar())


def check_budget(db: Session, *, niche_id: int | None) -> list[BudgetStatus]:
    """Every applicable ceiling for this scope: the system-wide one (always
    checked, if configured) plus the niche-specific one (if `niche_id` is
    given and a ceiling exists for it). A request can be blocked by either."""
    period_start = _current_month_start()
    statuses = []

    system_ceiling = _latest_ceiling(db, scope=BudgetScope.SYSTEM, niche_id=None)
    if system_ceiling is not None:
        spend = _compute_spend(db, niche_id=None, period_start=period_start)
        pct = spend / system_ceiling.monthly_limit_usd if system_ceiling.monthly_limit_usd > 0 else 0.0
        statuses.append(BudgetStatus(system_ceiling, spend, pct, pct >= 1.0))

    if niche_id is not None:
        niche_ceiling = _latest_ceiling(db, scope=BudgetScope.NICHE, niche_id=niche_id)
        if niche_ceiling is not None:
            spend = _compute_spend(db, niche_id=niche_id, period_start=period_start)
            pct = spend / niche_ceiling.monthly_limit_usd if niche_ceiling.monthly_limit_usd > 0 else 0.0
            statuses.append(BudgetStatus(niche_ceiling, spend, pct, pct >= 1.0))

    return statuses


def _maybe_alert(db: Session, status: BudgetStatus, notification_provider: NotificationProvider) -> None:
    already_alerted = status.ceiling.last_alert_threshold_pct or 0.0
    newly_crossed = [t for t in ALERT_THRESHOLDS if status.pct_used >= t > already_alerted]
    if not newly_crossed:
        return

    highest = max(newly_crossed)
    scope_desc = "system-wide" if status.ceiling.scope == BudgetScope.SYSTEM else f"niche {status.ceiling.niche_id}"
    subject = f"Budget {highest:.0%} threshold crossed ({scope_desc})"
    body = (
        f"Spend is ${status.spend_usd:.2f} of a ${status.ceiling.monthly_limit_usd:.2f} "
        f"monthly ceiling ({status.pct_used:.0%})."
    )
    request = NotificationRequest(
        severity=NotificationSeverity.CRITICAL if highest >= 1.0 else NotificationSeverity.WARNING,
        subject=subject,
        body=body,
        related_entity_type="budget_ceiling",
        related_entity_id=status.ceiling.id,
    )
    result = notification_provider.send(request)

    db.add(
        NotificationLog(
            channel=result.channel,
            severity=request.severity,
            subject=request.subject,
            body=request.body,
            sent_at=datetime.now(UTC),
            related_entity_type=request.related_entity_type,
            related_entity_id=request.related_entity_id,
        )
    )
    status.ceiling.last_alert_threshold_pct = highest
    db.flush()
    logger.warning("budget_alert_fired", threshold=highest, scope=scope_desc, delivered=result.delivered)


def enforce_budget(
    db: Session, *, niche_id: int | None, notification_provider: NotificationProvider
) -> list[BudgetStatus]:
    """Fires any newly-crossed alerts, then raises BudgetExceeded if any
    applicable ceiling is at or past 100%. Returns the checked statuses on
    success (informational — callers don't need to do anything with them)."""
    statuses = check_budget(db, niche_id=niche_id)
    for status in statuses:
        _maybe_alert(db, status, notification_provider)

    blocked = [s for s in statuses if s.is_blocked]
    if blocked:
        raise BudgetExceeded(blocked[0])

    return statuses
