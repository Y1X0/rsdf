"""Campaign workflow (goal #1): manual campaign input, storage, and Campaign
Intelligence scoring. Creation is idempotency-protected (adjustment #5)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from content_factory.api.deps import get_db
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.niche import Niche
from content_factory.logging_config import get_logger
from content_factory.schemas.campaign import CampaignCreate, CampaignOut, CampaignScoreOut
from content_factory.services import campaign_scoring, idempotency

logger = get_logger(__name__)
router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _to_campaign_out(campaign: Campaign) -> CampaignOut:
    out = CampaignOut.model_validate(campaign)
    if campaign.scores:
        out.latest_score = CampaignScoreOut.model_validate(campaign.scores[0])
    return out


def _get_or_create_niche(db: Session, niche_name: str | None) -> Niche | None:
    if not niche_name:
        return None
    niche = db.query(Niche).filter(Niche.name == niche_name).one_or_none()
    if niche is None:
        niche = Niche(name=niche_name)
        db.add(niche)
        db.flush()
    return niche


@router.post("", response_model=CampaignOut)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db)) -> CampaignOut:
    def _load_existing(campaign_id: int) -> Campaign:
        return db.get(Campaign, campaign_id)

    def _work() -> Campaign:
        niche = _get_or_create_niche(db, payload.niche_name)
        campaign = Campaign(
            whop_campaign_id=payload.whop_campaign_id,
            brand_name=payload.brand_name,
            niche_id=niche.id if niche else None,
            payout_model=payload.payout_model,
            cpm_rate=payload.cpm_rate,
            budget_cap=payload.budget_cap,
            rules_text=payload.rules_text,
            ai_content_allowed=payload.ai_content_allowed,
        )
        db.add(campaign)
        db.flush()
        return campaign

    campaign, created = idempotency.run_idempotent(
        db,
        scope="campaign.create",
        idempotency_key=payload.idempotency_key,
        payload=payload.model_dump(exclude={"idempotency_key"}),
        entity_type="campaign",
        work_fn=_work,
        load_existing=_load_existing,
    )
    logger.info("campaign_create_request", campaign_id=campaign.id, created=created)
    return _to_campaign_out(campaign)


@router.get("", response_model=list[CampaignOut])
def list_campaigns(db: Session = Depends(get_db)) -> list[CampaignOut]:
    campaigns = db.query(Campaign).order_by(Campaign.created_at.desc()).all()
    return [_to_campaign_out(c) for c in campaigns]


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: int, db: Session = Depends(get_db)) -> CampaignOut:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return _to_campaign_out(campaign)


@router.post("/{campaign_id}/score", response_model=CampaignScoreOut)
def score_campaign(campaign_id: int, db: Session = Depends(get_db)) -> CampaignScoreOut:
    """Deliberately not idempotency-gated: scoring is a cheap, deterministic
    read-mostly computation (not a paid external call), and ARCHITECTURE.md
    §3 treats re-scoring over time as a legitimate, expected event as
    competition/difficulty inputs change — so every call appends a new
    CampaignScore row rather than being deduplicated."""
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    score = campaign_scoring.score_campaign(db, campaign=campaign)
    return CampaignScoreOut.model_validate(score)
