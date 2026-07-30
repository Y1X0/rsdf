"""Active Cost Control Layer (ARCHITECTURE.md §10c) — Phase 2 M1.

Setting/raising a ceiling is always a human decision (§7.3: "budget
ceiling changes: human decides, every phase") — there is no endpoint that
lets the system raise its own ceiling. `GET /budget/status` is read-only
and never fires alerts (that only happens as a side effect of
`enforce_budget` on the actual cost-incurring endpoints); it exists purely
so an operator can check current spend without spending anything.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from content_factory.api.deps import get_db
from content_factory.api.pagination import Pagination, pagination_params
from content_factory.auth.dependencies import require_auth, require_operator
from content_factory.db.models.budget import BudgetCeiling
from content_factory.schemas.budget import BudgetCeilingCreate, BudgetCeilingOut, BudgetStatusOut
from content_factory.services import budget_governor

router = APIRouter(prefix="/budget", tags=["budget"])


@router.post("/ceilings", response_model=BudgetCeilingOut)
def create_ceiling(
    payload: BudgetCeilingCreate, db: Session = Depends(get_db), _principal: dict = Depends(require_operator)
) -> BudgetCeilingOut:
    ceiling = BudgetCeiling(
        scope=payload.scope, niche_id=payload.niche_id, monthly_limit_usd=payload.monthly_limit_usd
    )
    db.add(ceiling)
    db.flush()
    return BudgetCeilingOut.model_validate(ceiling)


@router.get("/ceilings", response_model=list[BudgetCeilingOut])
def list_ceilings(
    db: Session = Depends(get_db),
    pagination: Pagination = Depends(pagination_params),
    _principal: dict = Depends(require_auth),
) -> list[BudgetCeilingOut]:
    ceilings = (
        db.query(BudgetCeiling)
        .order_by(BudgetCeiling.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
        .all()
    )
    return [BudgetCeilingOut.model_validate(c) for c in ceilings]


@router.get("/status", response_model=list[BudgetStatusOut])
def get_status(
    niche_id: int | None = None, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> list[BudgetStatusOut]:
    statuses = budget_governor.check_budget(db, niche_id=niche_id)
    return [
        BudgetStatusOut(
            scope=s.ceiling.scope,
            niche_id=s.ceiling.niche_id,
            monthly_limit_usd=s.ceiling.monthly_limit_usd,
            spend_usd=s.spend_usd,
            pct_used=s.pct_used,
            is_blocked=s.is_blocked,
        )
        for s in statuses
    ]
