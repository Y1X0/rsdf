from datetime import UTC, datetime

from content_factory.db.models.campaign import Campaign
from content_factory.db.models.content import ResearchBrief
from content_factory.db.models.enums import HookSource, ProcessingStatus, ReviewDecisionType
from content_factory.db.models.niche import Niche
from content_factory.services import content_intelligence


def _make_niche(db_session, name="finance") -> Niche:
    niche = Niche(name=name)
    db_session.add(niche)
    db_session.flush()
    return niche


def test_ingest_research_brief_creates_hooks_and_patterns(db_session):
    niche = _make_niche(db_session)
    campaign = Campaign(brand_name="Acme", niche_id=niche.id)
    db_session.add(campaign)
    db_session.flush()

    brief = ResearchBrief(
        campaign_id=campaign.id,
        status=ProcessingStatus.COMPLETED,
        structured_data={
            "competitor_hooks": [{"hook_text": "3 things nobody tells you", "hook_type": "countdown"}],
            "competitor_patterns": [{"pattern_type": "hook", "description": "countdown format"}],
        },
        requested_at=datetime.now(UTC),
    )
    db_session.add(brief)
    db_session.flush()

    result = content_intelligence.ingest_research_brief(db_session, brief=brief, niche_id=niche.id)

    assert result == {"hooks_created": 1, "patterns_created": 1}
    hooks = content_intelligence.get_top_hooks(db_session, niche_id=niche.id)
    assert len(hooks) == 1
    assert hooks[0].source == HookSource.COMPETITOR_OBSERVED


def test_record_hook_outcome_updates_best_score_only_when_higher(db_session):
    niche = _make_niche(db_session)

    content_intelligence.record_hook_outcome(
        db_session, niche_id=niche.id, hook_text="Stop scrolling", viral_score=0.4
    )
    hook = content_intelligence.record_hook_outcome(
        db_session, niche_id=niche.id, hook_text="Stop scrolling", viral_score=0.2
    )
    assert hook.best_viral_score == 0.4  # lower score does not overwrite a better one

    hook = content_intelligence.record_hook_outcome(
        db_session, niche_id=niche.id, hook_text="Stop scrolling", viral_score=0.9
    )
    assert hook.best_viral_score == 0.9


def test_record_review_pattern_tags_known_bad_after_threshold(db_session):
    niche = _make_niche(db_session)

    first = content_intelligence.record_review_pattern(
        db_session, niche_id=niche.id, decision=ReviewDecisionType.REJECTED,
        reason_code="off_brand_tone", video_id=1,
    )
    assert first.outcome_tag is None  # first occurrence, not yet a confirmed bad pattern

    second = content_intelligence.record_review_pattern(
        db_session, niche_id=niche.id, decision=ReviewDecisionType.REJECTED,
        reason_code="off_brand_tone", video_id=2,
    )
    assert second.id == first.id
    assert second.outcome_tag == "known_bad"
    assert set(second.supporting_video_ids) == {1, 2}


def test_record_review_pattern_ignores_approvals(db_session):
    niche = _make_niche(db_session)
    pattern = content_intelligence.record_review_pattern(
        db_session, niche_id=niche.id, decision=ReviewDecisionType.APPROVED,
        reason_code=None, video_id=1,
    )
    assert pattern is None


def test_get_top_hooks_orders_by_best_viral_score_desc(db_session):
    niche = _make_niche(db_session)
    content_intelligence.record_hook_outcome(db_session, niche_id=niche.id, hook_text="low", viral_score=0.1)
    content_intelligence.record_hook_outcome(db_session, niche_id=niche.id, hook_text="high", viral_score=0.9)
    content_intelligence.find_or_create_hook(db_session, niche_id=niche.id, hook_text="unscored")

    hooks = content_intelligence.get_top_hooks(db_session, niche_id=niche.id, limit=10)
    assert hooks[0].hook_text == "high"
