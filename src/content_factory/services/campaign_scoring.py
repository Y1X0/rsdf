"""Campaign Intelligence (ARCHITECTURE.md §3) — Phase 1 heuristic version.

The formula matches §3.1 exactly:

    composite = 0.35*normalized(expected_roi) + 0.25*(1-competition)
              + 0.20*(1-difficulty) + 0.20*niche_fit

Phase 1 has no live competitor-saturation feed or historical CPM data yet.
**v1.1 (PHASE1_AUDIT.md F3):** `compute_competition_level` and
`compute_niche_fit_score` used to fall straight to a hardcoded 0.5 the
moment `Niche.saturation_score`/`trend_score` were unset — and there was no
API endpoint that could ever set them (see `api/routers/niches.py`, added
in this release, for the fix to that half of the problem). This half of
the fix changes what happens when they're *still* unset: instead of a
silent placeholder, these functions now derive a real signal from actual
internal data (how many campaigns already compete for this niche's
production slots; how the niche's own hooks have actually performed) before
falling back to the neutral default — and `breakdown_json` always records
which of the three (`manual`, an internal-derivation, or genuine
`insufficient_data`) produced each number, so nobody mistakes a fallback
for a real reading.
"""

import math

from sqlalchemy.orm import Session

from content_factory.db.models.campaign import Campaign, CampaignScore
from content_factory.db.models.enums import ProcessingStatus
from content_factory.db.models.hook import HookLibrary
from content_factory.db.models.niche import Niche
from content_factory.logging_config import get_logger

logger = get_logger(__name__)

# Illustrative default assumptions, used only when no real Cost Control
# Layer / Revenue Optimization history exists yet for this niche. See
# ARCHITECTURE.md §18's cost table and §13's view-outcome scenario framing.
DEFAULT_ASSUMED_COST_PER_VIDEO_USD = 6.0
DEFAULT_ASSUMED_VIEWS = {"low": 500, "median": 3_000, "high": 15_000}

# How many *other* campaigns already in a niche count as "fully saturated"
# for the internal-signal fallback, absent a manually-set saturation_score.
# Illustrative, tunable — this is a proxy for internal production-slot
# competition, not external market saturation (Phase 1 has no competitor
# feed to measure that with; see ARCHITECTURE.md §4.1).
SATURATION_CAMPAIGN_THRESHOLD = 10

DEFAULT_NEUTRAL_SCORE = 0.5

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


def compute_competition_level(db: Session, *, campaign: Campaign, niche: Niche | None) -> tuple[float, str]:
    """Returns (score, source). 0 (uncontested) .. 1 (saturated).

    Precedence: an operator's manually-set `Niche.saturation_score` always
    wins (they know things this system can't measure). Absent that, derive
    a real signal from how many *other* campaigns already exist in this
    niche — genuine internal data, not a guess. Only when neither is
    available does this fall back to the neutral default, and that fallback
    is always labeled as such in the returned source tag.
    """
    if niche is not None and niche.saturation_score is not None:
        return round(max(0.0, min(niche.saturation_score, 1.0)), 3), "manual"

    if niche is not None:
        other_campaign_count = (
            db.query(Campaign)
            .filter(Campaign.niche_id == niche.id, Campaign.id != campaign.id)
            .count()
        )
        if other_campaign_count > 0:
            score = min(other_campaign_count / SATURATION_CAMPAIGN_THRESHOLD, 1.0)
            return round(score, 3), "internal_campaign_count"

    return DEFAULT_NEUTRAL_SCORE, "insufficient_data"


def compute_niche_fit_score(db: Session, *, niche: Niche | None) -> tuple[float, str]:
    """Returns (score, source). 0 (poor fit) .. 1 (great fit).

    Same precedence as competition_level: manual `Niche.trend_score` wins;
    absent that, derive a real signal from the average `best_viral_score`
    of hooks already observed in this niche (real outcome data, when any
    exists); only then fall back to the neutral default.
    """
    if niche is not None and niche.trend_score is not None:
        return round(max(0.0, min(niche.trend_score, 1.0)), 3), "manual"

    if niche is not None:
        scored_hooks = (
            db.query(HookLibrary.best_viral_score)
            .filter(HookLibrary.niche_id == niche.id, HookLibrary.best_viral_score.isnot(None))
            .all()
        )
        scores = [row[0] for row in scored_hooks]
        if scores:
            average = sum(scores) / len(scores)
            return round(max(0.0, min(average, 1.0)), 3), "internal_hook_performance"

    return DEFAULT_NEUTRAL_SCORE, "insufficient_data"


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
    competition, competition_source = compute_competition_level(db, campaign=campaign, niche=niche)
    niche_fit, niche_fit_source = compute_niche_fit_score(db, niche=niche)
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
            "competition_source": competition_source,
            "niche_fit_source": niche_fit_source,
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
        competition_source=competition_source,
        niche_fit_source=niche_fit_source,
    )
    return score
