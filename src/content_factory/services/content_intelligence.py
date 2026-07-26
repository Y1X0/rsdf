"""Content Intelligence Layer (ARCHITECTURE.md §4) — Phase 1 scope:
hook_library (§4.2) and learning_patterns (§4.3) storage/retrieval, and
outcome tagging fed by real review decisions and real analytics (goal #4:
"store observed patterns, track successful/failed patterns").

Retrieval here is plain SQL filter+sort (niche, recency, score) rather than
vector similarity search — see the Phase 1 plan's "vector search deferred"
decision. Swapping in embedding-based retrieval later only changes
`get_top_hooks`; callers (agents/script_agent.py) are unaffected.
"""

from sqlalchemy import nullslast
from sqlalchemy.orm import Session

from content_factory.db.models.content import ContentIdea, ResearchBrief
from content_factory.db.models.enums import HookSource, PatternConfidenceTier, PatternType, ReviewDecisionType
from content_factory.db.models.hook import HookLibrary, LearningPattern
from content_factory.logging_config import get_logger
from content_factory.services import db_safety

logger = get_logger(__name__)

# How many times the same rejection reason must recur (for the same niche)
# before the underlying pattern is tagged known-bad. See ARCHITECTURE.md
# §7.1: "a pattern or account rejected repeatedly for the same reason
# becomes a known-bad-pattern entry."
REJECTION_PATTERN_THRESHOLD = 2

# Caps how many ContentIdea rows one research brief can auto-create — the
# LLM is free to return more angles, but nothing downstream (script
# generation, rendering) should scale unboundedly with however many
# angles a single response happened to list.
MAX_AUTO_GENERATED_IDEAS_PER_BRIEF = 5

_VALID_PATTERN_TYPES = {p.value for p in PatternType}


def ingest_research_brief(db: Session, *, brief: ResearchBrief, niche_id: int | None) -> dict:
    """Persists the Research Agent's structured output into hook_library,
    learning_patterns, and — closing the "Campaign -> Research -> Ideas"
    automatic transition — content_ideas. Competitor material is always
    tagged COMPETITOR_OBSERVED per §4.1's "inspiration only, never verbatim"
    rule — this function does not attempt to detect verbatim copying itself
    (that's the Script Agent's job to avoid at generation time); it just
    labels provenance honestly.
    """
    data = brief.structured_data or {}
    hooks_created = 0
    for hook_data in data.get("competitor_hooks", []):
        hook_text = (hook_data.get("hook_text") or "").strip()
        if not hook_text:
            continue
        db.add(
            HookLibrary(
                niche_id=niche_id,
                hook_text=hook_text,
                hook_type=hook_data.get("hook_type"),
                source=HookSource.COMPETITOR_OBSERVED,
            )
        )
        hooks_created += 1

    patterns_created = 0
    for pattern_data in data.get("competitor_patterns", []):
        raw_type = pattern_data.get("pattern_type", "structure")
        pattern_type = PatternType(raw_type) if raw_type in _VALID_PATTERN_TYPES else PatternType.STRUCTURE
        description = (pattern_data.get("description") or "").strip()
        if not description:
            continue
        db.add(
            LearningPattern(
                niche_id=niche_id,
                pattern_type=pattern_type,
                description=description,
                confidence_tier=PatternConfidenceTier.CANDIDATE,
                supporting_video_ids=[],
            )
        )
        patterns_created += 1

    ideas_created = 0
    for angle in data.get("recommended_angles", [])[:MAX_AUTO_GENERATED_IDEAS_PER_BRIEF]:
        concept_summary = (angle or "").strip()
        if not concept_summary:
            continue
        db.add(
            ContentIdea(
                campaign_id=brief.campaign_id,
                concept_summary=concept_summary,
                source="research_agent",
            )
        )
        ideas_created += 1

    db.flush()
    logger.info(
        "content_intelligence_ingested_brief",
        brief_id=brief.id,
        hooks_created=hooks_created,
        patterns_created=patterns_created,
        ideas_created=ideas_created,
    )
    return {"hooks_created": hooks_created, "patterns_created": patterns_created, "ideas_created": ideas_created}


def find_or_create_hook(
    db: Session, *, niche_id: int | None, hook_text: str, hook_type: str | None = None,
    source: HookSource = HookSource.INTERNAL,
) -> HookLibrary:
    """Safe upsert (PHASE1_AUDIT.md F5): this is called on essentially
    every script generation and every metrics submission, making it the
    more likely of the two known check-then-act races (the other being
    niche creation) to actually manifest under real concurrent traffic. A
    unique constraint on `(niche_id, hook_text)` backs this — note that,
    per standard SQL NULL semantics, two hooks with `niche_id IS NULL` and
    identical text are *not* considered duplicates by that constraint, so
    the race is only fully closed for hooks that have a real niche
    assigned. In practice every current caller always supplies one.
    """

    def _query() -> HookLibrary | None:
        return (
            db.query(HookLibrary)
            .filter(HookLibrary.niche_id == niche_id, HookLibrary.hook_text == hook_text)
            .one_or_none()
        )

    def _create() -> HookLibrary:
        hook = HookLibrary(niche_id=niche_id, hook_text=hook_text, hook_type=hook_type, source=source)
        db.add(hook)
        return hook

    return db_safety.get_or_create(db, query=_query, create=_create)


def record_hook_usage(db: Session, *, niche_id: int | None, hook_text: str) -> HookLibrary:
    hook = find_or_create_hook(db, niche_id=niche_id, hook_text=hook_text)
    hook.times_used += 1
    db.flush()
    return hook


def record_hook_outcome(
    db: Session, *, niche_id: int | None, hook_text: str, viral_score: float,
    retention_at_3s: float | None = None,
) -> HookLibrary:
    """Called from services/analytics_service.py once a real viral score
    exists for a video — this is the "track successful/failed patterns"
    half of goal #4 for hooks specifically."""
    hook = find_or_create_hook(db, niche_id=niche_id, hook_text=hook_text)
    if hook.best_viral_score is None or viral_score > hook.best_viral_score:
        hook.best_viral_score = viral_score
    if retention_at_3s is not None:
        hook.retention_at_3s = retention_at_3s
    db.flush()
    logger.info("hook_outcome_recorded", hook_id=hook.id, viral_score=viral_score)
    return hook


def get_top_hooks(db: Session, *, niche_id: int | None, limit: int = 5, offset: int = 0) -> list[HookLibrary]:
    query = db.query(HookLibrary)
    if niche_id is not None:
        query = query.filter(HookLibrary.niche_id == niche_id)
    query = query.order_by(
        nullslast(HookLibrary.best_viral_score.desc()), HookLibrary.created_at.desc()
    )
    return query.offset(offset).limit(limit).all()


def get_patterns(
    db: Session,
    *,
    niche_id: int | None = None,
    tier: PatternConfidenceTier | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[LearningPattern]:
    query = db.query(LearningPattern)
    if niche_id is not None:
        query = query.filter(LearningPattern.niche_id == niche_id)
    if tier is not None:
        query = query.filter(LearningPattern.confidence_tier == tier)
    return query.order_by(LearningPattern.created_at.desc()).offset(offset).limit(limit).all()


def record_review_pattern(
    db: Session, *, niche_id: int | None, decision: ReviewDecisionType,
    reason_code: str | None, video_id: int,
) -> LearningPattern | None:
    """The "known-bad pattern" half of ARCHITECTURE.md §7.1: a rejection
    reason that recurs REJECTION_PATTERN_THRESHOLD times for the same niche
    is tagged known_bad so the Script Agent's retrieval (once patterns
    influence generation directly, Phase 2) can avoid repeating it. Only
    rejections are tracked here — approvals don't need an equivalent
    "known-good structural pattern" in Phase 1 since hook-level success is
    already captured by record_hook_outcome via real viral scores, which is
    a stronger signal than "a human clicked approve."
    """
    if decision != ReviewDecisionType.REJECTED or not reason_code:
        return None

    description = f"repeated_rejection:{reason_code}"
    pattern = (
        db.query(LearningPattern)
        .filter(
            LearningPattern.niche_id == niche_id,
            LearningPattern.pattern_type == PatternType.STRUCTURE,
            LearningPattern.description == description,
        )
        .one_or_none()
    )
    if pattern is None:
        pattern = LearningPattern(
            niche_id=niche_id,
            pattern_type=PatternType.STRUCTURE,
            description=description,
            confidence_tier=PatternConfidenceTier.CANDIDATE,
            supporting_video_ids=[],
        )
        db.add(pattern)
        db.flush()

    supporting_ids = list(pattern.supporting_video_ids or [])
    if video_id not in supporting_ids:
        supporting_ids.append(video_id)
    pattern.supporting_video_ids = supporting_ids
    pattern.confidence_score = float(len(supporting_ids))

    if len(supporting_ids) >= REJECTION_PATTERN_THRESHOLD:
        pattern.outcome_tag = "known_bad"

    db.flush()
    logger.info(
        "review_pattern_recorded",
        pattern_id=pattern.id,
        reason_code=reason_code,
        occurrence_count=len(supporting_ids),
        outcome_tag=pattern.outcome_tag,
    )
    return pattern
