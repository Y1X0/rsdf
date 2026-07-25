"""Experimentation Engine (ARCHITECTURE.md §5) — Phase 2 M6.

`POST /experimentation/run` only ever writes `experiment_results` rows —
recommend-only, per §7.2/§16. `POST /experimentation/recommendations/{id}/
apply` is the separate, explicit human action that actually applies one;
nothing here auto-applies a winner just because it was computed.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from content_factory.api.deps import get_db
from content_factory.auth.dependencies import require_auth, require_operator
from content_factory.db.models.experiment import Experiment, ExperimentResult
from content_factory.schemas.experiment import (
    ApplyRecommendationRequest,
    ExperimentOut,
    ExperimentResultOut,
    RunExperimentRequest,
    RunExperimentResponse,
)
from content_factory.services import experimentation_service
from content_factory.services.experimentation_service import RecommendationNotWinner

router = APIRouter(prefix="/experimentation", tags=["experimentation"])


@router.post("/run", response_model=RunExperimentResponse)
def run_experiment(
    payload: RunExperimentRequest,
    db: Session = Depends(get_db),
    _principal: dict = Depends(require_operator),
) -> RunExperimentResponse:
    experiment = experimentation_service.run_experiment(
        db,
        axis=payload.axis,
        niche_id=payload.niche_id,
        min_sample_size=payload.min_sample_size,
        significance_threshold=payload.significance_threshold,
    )
    results = (
        db.query(ExperimentResult)
        .filter(ExperimentResult.experiment_id == experiment.id)
        .order_by(ExperimentResult.avg_viral_score.desc())
        .all()
    )
    return RunExperimentResponse(
        experiment=ExperimentOut.model_validate(experiment),
        results=[ExperimentResultOut.model_validate(r) for r in results],
    )


@router.get("/recommendations", response_model=list[ExperimentResultOut])
def list_recommendations(
    winners_only: bool = True,
    db: Session = Depends(get_db),
    _principal: dict = Depends(require_auth),
) -> list[ExperimentResultOut]:
    query = db.query(ExperimentResult)
    if winners_only:
        query = query.filter(ExperimentResult.is_winner.is_(True))
    results = query.order_by(ExperimentResult.computed_at.desc()).all()
    return [ExperimentResultOut.model_validate(r) for r in results]


@router.post("/recommendations/{result_id}/apply", response_model=ExperimentResultOut)
def apply_recommendation(
    result_id: int,
    payload: ApplyRecommendationRequest,
    db: Session = Depends(get_db),
    _principal: dict = Depends(require_operator),
) -> ExperimentResultOut:
    result = db.get(ExperimentResult, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Experiment result not found")

    try:
        applied = experimentation_service.apply_recommendation(db, result=result, applied_by=payload.applied_by)
    except RecommendationNotWinner as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    return ExperimentResultOut.model_validate(applied)


@router.get("/{experiment_id}", response_model=ExperimentOut)
def get_experiment(
    experiment_id: int, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> ExperimentOut:
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return ExperimentOut.model_validate(experiment)
