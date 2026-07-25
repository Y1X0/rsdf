"""Quality Scoring System (ARCHITECTURE.md §6) — minimal heuristic version,
approved for Phase 1. Only originality and policy-risk are computed from
real logic; retention_prediction and monetization_probability are left null
(the DB column exists so a Phase 2 learned model can populate them without a
migration — see db/models/quality.py). The reviewer dashboard must treat
null as "not yet available," never as zero.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from content_factory.config import Settings
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.content import ContentIdea, Script
from content_factory.db.models.hook import HookLibrary
from content_factory.db.models.quality import QualityScore
from content_factory.db.models.video import Video
from content_factory.logging_config import get_logger

logger = get_logger(__name__)

# Keyword-based policy risk heuristic (ARCHITECTURE.md §6c). A real
# classifier is a Phase 2+ upgrade; this Phase 1 version exists so the
# reviewer always sees *something* concrete rather than an empty field.
_BANNED_CLAIM_KEYWORDS = (
    "guaranteed",
    "cure",
    "risk-free",
    "100% proven",
    "get rich quick",
    "no risk",
    "instant results",
)
_DISCLOSURE_MARKERS = ("#ad", "sponsored", "#sponsored", "paid partnership")


def compute_originality_score(db: Session, *, script: Script, niche_id: int | None) -> float:
    """0 (near-duplicate) .. 100 (fully original). Jaccard word-set overlap
    against prior scripts in the same niche and any competitor-observed
    hooks (ARCHITECTURE.md §4.1) — a Phase 1 heuristic in place of the
    embedding-similarity approach described in §6a."""
    candidate_words = set(script.full_text.lower().split())
    if not candidate_words:
        return 100.0

    comparison_texts: list[str] = []
    if niche_id is not None:
        prior_scripts = (
            db.query(Script)
            .join(ContentIdea, Script.idea_id == ContentIdea.id)
            .join(Campaign, ContentIdea.campaign_id == Campaign.id)
            .filter(Campaign.niche_id == niche_id, Script.id != script.id)
            .all()
        )
        comparison_texts.extend(s.full_text for s in prior_scripts)
        hooks = db.query(HookLibrary).filter(HookLibrary.niche_id == niche_id).all()
        comparison_texts.extend(h.hook_text for h in hooks)

    if not comparison_texts:
        return 100.0

    max_similarity = 0.0
    for text in comparison_texts:
        other_words = set(text.lower().split())
        if not other_words:
            continue
        union = len(candidate_words | other_words)
        if union == 0:
            continue
        similarity = len(candidate_words & other_words) / union
        max_similarity = max(max_similarity, similarity)

    return round((1 - max_similarity) * 100, 2)


def compute_policy_risk_score(*, script: Script, campaign: Campaign) -> float:
    """0 (low risk) .. 100 (high risk)."""
    text = f"{script.hook_text} {script.full_text} {script.cta_text or ''}".lower()
    risk = 0.0
    for keyword in _BANNED_CLAIM_KEYWORDS:
        if keyword in text:
            risk += 25.0

    requires_disclosure = bool(campaign.rules_text and "disclos" in campaign.rules_text.lower())
    has_disclosure_marker = any(marker in text for marker in _DISCLOSURE_MARKERS)
    if requires_disclosure and not has_disclosure_marker:
        risk += 15.0

    return round(min(risk, 100.0), 2)


def score_video(
    db: Session, *, video: Video, script: Script, campaign: Campaign, niche_id: int | None
) -> QualityScore:
    originality = compute_originality_score(db, script=script, niche_id=niche_id)
    policy_risk = compute_policy_risk_score(script=script, campaign=campaign)

    quality = QualityScore(
        video_id=video.id,
        originality_score=originality,
        retention_prediction_score=None,
        policy_risk_score=policy_risk,
        monetization_probability_score=None,
        model_version="heuristic-v1",
        computed_at=datetime.now(UTC),
    )
    db.add(quality)
    db.flush()

    logger.info(
        "quality_score_computed",
        video_id=video.id,
        originality_score=originality,
        policy_risk_score=policy_risk,
    )
    return quality


def determine_auto_reject_reason(quality: QualityScore, settings: Settings) -> str | None:
    """Phase 2 M2 threshold gating. Opt-in and disabled by default (see
    config.py's Settings docstring): with the default floor/ceiling, neither
    branch below can ever be true, so Phase 1's "informational only"
    behavior is preserved exactly until an operator sets real thresholds.

    Returns a reason_code (reused as-is by review_service.submit_review and
    content_intelligence.record_review_pattern's known-bad-pattern feedback
    loop) or None if the video should proceed to normal human review.
    """
    if quality.originality_score is not None and quality.originality_score < settings.quality_originality_auto_reject_floor:
        return "auto_reject:originality_below_floor"
    if (
        quality.policy_risk_score is not None
        and quality.policy_risk_score > settings.quality_policy_risk_auto_reject_ceiling
    ):
        return "auto_reject:policy_risk_above_ceiling"
    return None
