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

    assert result == {"hooks_created": 1, "patterns_created": 1, "ideas_created": 0}
    hooks = content_intelligence.get_top_hooks(db_session, niche_id=niche.id)
    assert len(hooks) == 1
    assert hooks[0].source == HookSource.COMPETITOR_OBSERVED


def test_ingest_research_brief_auto_generates_ideas_from_recommended_angles(db_session):
    from content_factory.db.models.content import ContentIdea

    niche = _make_niche(db_session)
    campaign = Campaign(brand_name="Acme", niche_id=niche.id)
    db_session.add(campaign)
    db_session.flush()

    brief = ResearchBrief(
        campaign_id=campaign.id,
        status=ProcessingStatus.COMPLETED,
        structured_data={"recommended_angles": ["Budgeting myths", "Quick win tips"]},
        requested_at=datetime.now(UTC),
    )
    db_session.add(brief)
    db_session.flush()

    result = content_intelligence.ingest_research_brief(db_session, brief=brief, niche_id=niche.id)

    assert result["ideas_created"] == 2
    ideas = db_session.query(ContentIdea).filter(ContentIdea.campaign_id == campaign.id).all()
    assert len(ideas) == 2
    assert {i.concept_summary for i in ideas} == {"Budgeting myths", "Quick win tips"}
    assert all(i.source == "research_agent" for i in ideas)
    assert all(i.status == "proposed" for i in ideas)


def test_ingest_research_brief_caps_auto_generated_ideas(db_session):
    from content_factory.db.models.content import ContentIdea

    niche = _make_niche(db_session)
    campaign = Campaign(brand_name="Acme", niche_id=niche.id)
    db_session.add(campaign)
    db_session.flush()

    brief = ResearchBrief(
        campaign_id=campaign.id,
        status=ProcessingStatus.COMPLETED,
        structured_data={"recommended_angles": [f"angle {i}" for i in range(20)]},
        requested_at=datetime.now(UTC),
    )
    db_session.add(brief)
    db_session.flush()

    result = content_intelligence.ingest_research_brief(db_session, brief=brief, niche_id=niche.id)

    assert result["ideas_created"] == content_intelligence.MAX_AUTO_GENERATED_IDEAS_PER_BRIEF
    ideas = db_session.query(ContentIdea).filter(ContentIdea.campaign_id == campaign.id).all()
    assert len(ideas) == content_intelligence.MAX_AUTO_GENERATED_IDEAS_PER_BRIEF


def test_ingest_research_brief_skips_blank_angles(db_session):
    from content_factory.db.models.content import ContentIdea

    niche = _make_niche(db_session)
    campaign = Campaign(brand_name="Acme", niche_id=niche.id)
    db_session.add(campaign)
    db_session.flush()

    brief = ResearchBrief(
        campaign_id=campaign.id,
        status=ProcessingStatus.COMPLETED,
        structured_data={"recommended_angles": ["", "   ", "Real angle"]},
        requested_at=datetime.now(UTC),
    )
    db_session.add(brief)
    db_session.flush()

    result = content_intelligence.ingest_research_brief(db_session, brief=brief, niche_id=niche.id)

    assert result["ideas_created"] == 1
    ideas = db_session.query(ContentIdea).filter(ContentIdea.campaign_id == campaign.id).all()
    assert len(ideas) == 1
    assert ideas[0].concept_summary == "Real angle"


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


def test_get_top_hooks_diversifies_across_hook_types_instead_of_one_type_dominating(db_session):
    """Regression test for a real gap: a single hook_type/framework that
    happens to score highest across the board used to crowd out every
    other type entirely - ScriptAgent/ClipSelectionAgent would only ever
    see one "proven" pattern, biasing everything generated toward it."""
    niche = _make_niche(db_session)
    # Three curiosity_gap hooks, all scoring higher than the single
    # bold_claim hook - without diversification, bold_claim would never
    # appear in the top 2.
    content_intelligence.find_or_create_hook(
        db_session, niche_id=niche.id, hook_text="cg1", hook_type="curiosity_gap"
    )
    content_intelligence.record_hook_outcome(
        db_session, niche_id=niche.id, hook_text="cg1", viral_score=0.95
    )
    content_intelligence.find_or_create_hook(
        db_session, niche_id=niche.id, hook_text="cg2", hook_type="curiosity_gap"
    )
    content_intelligence.record_hook_outcome(
        db_session, niche_id=niche.id, hook_text="cg2", viral_score=0.9
    )
    content_intelligence.find_or_create_hook(
        db_session, niche_id=niche.id, hook_text="bc1", hook_type="bold_claim"
    )
    content_intelligence.record_hook_outcome(
        db_session, niche_id=niche.id, hook_text="bc1", viral_score=0.5
    )

    hooks = content_intelligence.get_top_hooks(db_session, niche_id=niche.id, limit=2)

    hook_types = {h.hook_type for h in hooks}
    assert hook_types == {"curiosity_gap", "bold_claim"}


def test_get_top_hooks_still_returns_best_first_within_a_single_type(db_session):
    """When there's only one distinct hook_type (or none at all - the
    common case for hooks recorded before this feature existed),
    diversification must degrade to plain best-first ordering, not
    change existing behavior."""
    niche = _make_niche(db_session)
    content_intelligence.record_hook_outcome(db_session, niche_id=niche.id, hook_text="low", viral_score=0.1)
    content_intelligence.record_hook_outcome(db_session, niche_id=niche.id, hook_text="high", viral_score=0.9)

    hooks = content_intelligence.get_top_hooks(db_session, niche_id=niche.id, limit=2)
    assert [h.hook_text for h in hooks] == ["high", "low"]
