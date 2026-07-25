"""Campaign Intelligence (ARCHITECTURE.md §3) — Phase 1 heuristic version.

The formula matches §3.1 exactly:

    composite = 0.35*normalized(expected_roi) + 0.25*(1-competition)
              + 0.20*(1-difficulty) + 0.20*niche_fit

Phase 1 has no live competitor-saturation feed or historical CPM data yet,
so `difficulty`, `competition`, and `niche_fit` are computed from whatever
is available (campaign rules text, manually-maintained Niche fields) with
clearly documented neutral defaults when data is missing — never a silent
guess presented as a confident number. As the Content Intelligence Layer
(services/content_intelligence.py) and Revenue Optimization data accumulate
in later phases, these functions are the only things that need to change;
the composite formula and the CampaignScore schema stay the same.
"""

import math

from sqlalchemy.orm import Session

from content_factory.db.models.campaign import Campaign, CampaignScore
from content_factory.db.models.enums import ProcessingStatus
from content_factory.db.models.niche import Niche
from content_factory.logging_config import get_logger

logger = get_logger(__name__)

# Illustrative default assumptions, used only when no real Cost Control
# Layer / Revenue Optimization history exists yet for this niche. See
# ARCHITECTURE.md §18's cost table and §13's view-outcome scenario framing.
DEFAULT_ASSUMED_COST_PER_VIDEO_USD = 6.0
DEFAULT_ASSUMED_VIEWS = {"low": 500, "median": 3_000, "high": 15_000}

_DIFFICULTY_KEYWORDS = (
    "exclusive",
    "no ai",
    "must not use ai",
    "organic only",
    "verified account",
    "minimum follower",
    "brand asset",
    "approval required",
)

PROCEED_THRESHOLD = 0.6
TEST_BATCH_THRESHOLD = 0.45


def compute_difficulty_score(campaign: Campaign) -> float:
    """0 (easy) .. 1 (hard). Heuristic keyword scan over the operator's
    transcribed rules text, plus a mild length penalty (more rules text
    usually means more constraints to satisfy correctly)."""
    text = (campaign.rules_text or "").lower()
    score = 0.1
    for keyword in _DIFFICULTY_KEYWORDS:
        if keyword in text:
            score += 0.15
    length_penalty = min(len(text) / 2000, 0.3)
    return round(min(score + length_penalty, 1.0), 3)


def compute_competition_level(niche: Niche | None) -> float:
    """0 (uncontested) .. 1 (saturated). Reads Niche.saturation_score,
    maintained manually in Phase 1 (populated automatically by competitor
    tracking in Phase 2, ARCHITECTURE.md §4.1). Defaults to a neutral 0.5
    when unknown, rather than assuming either extreme."""
    if niche is not None and niche.saturation_score is not None:
        return round(max(0.0, min(niche.saturation_score, 1.0)), 3)
    return 0.5


def compute_niche_fit_score(niche: Niche | None) -> float:
    """0 (poor fit) .. 1 (great fit). Reads Niche.trend_score, same
    Phase 1/Phase 2 provenance note as competition_level."""
    if niche is not None and niche.trend_score is not None:
        return round(max(0.0, min(niche.trend_score, 1.0)), 3)
    return 0.5


def compute_expected_roi(
    campaign: Campaign, assumed_cost_per_video: float = DEFAULT_ASSUMED_COST_PER_VIDEO_USD
) -> tuple[float, float, float]:
    """Returns (low, median, high) profit-per-video estimates in USD across
    conservative/median/optimistic view-outcome scenarios, per §3.4. Always
    a range, never a single number — pre-publish performance prediction is
    inherently uncertain."""
    cpm = campaign.cpm_rate or 0.0
    low = (DEFAULT_ASSUMED_VIEWS["low"] / 1000) * cpm - assumed_cost_per_video
    median = (DEFAULT_ASSUMED_VIEWS["median"] / 1000) * cpm - assumed_cost_per_video
    high = (DEFAULT_ASSUMED_VIEWS["high"] / 1000) * cpm - assumed_cost_per_video
    return round(low, 2), round(median, 2), round(high, 2)


def _normalize_roi(roi_median: float, scale: float = 50.0) -> float:
    """Squashes an unbounded USD profit estimate into 0..1 via a logistic
    curve so it can be blended with the other 0..1 sub-scores."""
    return 1 / (1 + math.exp(-roi_median / scale))


def score_campaign(db: Session, *, campaign: Campaign) -> CampaignScore:
    niche = campaign.niche
    difficulty = compute_difficulty_score(campaign)
    competition = compute_competition_level(niche)
    niche_fit = compute_niche_fit_score(niche)
    roi_low, roi_median, roi_high = compute_expected_roi(campaign)
    normalized_roi = _normalize_roi(roi_median)

    composite = (
        0.35 * normalized_roi
        + 0.25 * (1 - competition)
        + 0.20 * (1 - difficulty)
        + 0.20 * niche_fit
    )
    composite = round(composite, 4)

    # Guardrail from §3.4: never recommend full production when the
    # pessimistic case is net-negative, regardless of the composite score.
    if composite >= PROCEED_THRESHOLD and roi_low >= 0:
        recommendation = "proceed"
    elif composite >= TEST_BATCH_THRESHOLD:
        recommendation = "test_batch_only"
    else:
        recommendation = "reject"

    score = CampaignScore(
        campaign_id=campaign.id,
        status=ProcessingStatus.COMPLETED,
        expected_roi_low=roi_low,
        expected_roi_median=roi_median,
        expected_roi_high=roi_high,
        difficulty_score=difficulty,
        competition_level=competition,
        niche_fit_score=niche_fit,
        composite_score=composite,
        recommendation=recommendation,
        breakdown_json={
            "normalized_roi": round(normalized_roi, 4),
            "assumed_cost_per_video_usd": DEFAULT_ASSUMED_COST_PER_VIDEO_USD,
            "assumed_views": DEFAULT_ASSUMED_VIEWS,
        },
    )
    db.add(score)
    db.flush()

    logger.info(
        "campaign_scored",
        campaign_id=campaign.id,
        composite_score=composite,
        recommendation=recommendation,
        roi_low=roi_low,
        roi_median=roi_median,
    )
    return score
