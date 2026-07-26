"""v1.1 addition (PHASE1_AUDIT.md F3): the manual data-entry path Campaign
Intelligence scoring needed but never had — without this, `Niche.saturation_score`
/`trend_score`/`avg_cpm_est` could never be set by anyone, so
services/campaign_scoring.py's competition/niche-fit inputs were
permanently stuck at their neutral default."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from content_factory.api.deps import get_db
from content_factory.api.pagination import Pagination, pagination_params
from content_factory.auth.dependencies import require_auth, require_operator
from content_factory.db.models.niche import Niche
from content_factory.schemas.analytics import ProfitSummaryOut
from content_factory.schemas.niche import NicheCreate, NicheOut, NicheUpdate
from content_factory.services import analytics_service

router = APIRouter(prefix="/niches", tags=["niches"])


@router.post("", response_model=NicheOut)
def create_niche(
    payload: NicheCreate, db: Session = Depends(get_db), _principal: dict = Depends(require_operator)
) -> NicheOut:
    existing = db.query(Niche).filter(Niche.name == payload.name).one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Niche {payload.name!r} already exists")

    try:
        with db.begin_nested():
            niche = Niche(
                name=payload.name,
                category=payload.category,
                saturation_score=payload.saturation_score,
                avg_cpm_est=payload.avg_cpm_est,
                trend_score=payload.trend_score,
            )
            db.add(niche)
            db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"Niche {payload.name!r} already exists") from None

    return NicheOut.model_validate(niche)


@router.get("", response_model=list[NicheOut])
def list_niches(
    db: Session = Depends(get_db),
    pagination: Pagination = Depends(pagination_params),
    _principal: dict = Depends(require_auth),
) -> list[NicheOut]:
    niches = db.query(Niche).order_by(Niche.name.asc()).offset(pagination.offset).limit(pagination.limit).all()
    return [NicheOut.model_validate(n) for n in niches]


@router.get("/{niche_id}", response_model=NicheOut)
def get_niche(
    niche_id: int, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> NicheOut:
    niche = db.get(Niche, niche_id)
    if niche is None:
        raise HTTPException(status_code=404, detail="Niche not found")
    return NicheOut.model_validate(niche)


@router.patch("/{niche_id}", response_model=NicheOut)
def update_niche(
    niche_id: int,
    payload: NicheUpdate,
    db: Session = Depends(get_db),
    _principal: dict = Depends(require_operator),
) -> NicheOut:
    niche = db.get(Niche, niche_id)
    if niche is None:
        raise HTTPException(status_code=404, detail="Niche not found")

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(niche, field, value)
    db.flush()

    return NicheOut.model_validate(niche)


@router.get("/{niche_id}/profit", response_model=ProfitSummaryOut)
def get_niche_profit(
    niche_id: int, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> ProfitSummaryOut:
    """ARCHITECTURE.md §9's per-niche profit rollup (Phase 2 M6)."""
    niche = db.get(Niche, niche_id)
    if niche is None:
        raise HTTPException(status_code=404, detail="Niche not found")
    summary = analytics_service.compute_niche_profit_summary(db, niche_id=niche_id)
    return ProfitSummaryOut(**summary)
