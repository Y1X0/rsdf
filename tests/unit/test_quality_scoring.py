from content_factory.config import Settings
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.content import ContentIdea, Script
from content_factory.db.models.enums import ProcessingStatus
from content_factory.db.models.niche import Niche
from content_factory.db.models.quality import QualityScore
from content_factory.services import quality_scoring


def _make_script(db_session, *, full_text: str, niche_id=None, rules_text=None) -> tuple[Script, Campaign]:
    campaign = Campaign(brand_name="Acme", niche_id=niche_id, rules_text=rules_text)
    db_session.add(campaign)
    db_session.flush()
    idea = ContentIdea(campaign_id=campaign.id, concept_summary="test idea")
    db_session.add(idea)
    db_session.flush()
    script = Script(
        idea_id=idea.id,
        variant_label="v1",
        hook_text="hook",
        full_text=full_text,
        generation_status=ProcessingStatus.COMPLETED,
    )
    db_session.add(script)
    db_session.flush()
    return script, campaign


def test_originality_score_is_100_with_no_prior_content(db_session):
    script, _ = _make_script(db_session, full_text="a completely unique script about gardening")
    score = quality_scoring.compute_originality_score(db_session, script=script, niche_id=None)
    assert score == 100.0


def test_originality_score_drops_for_near_duplicate_script(db_session):
    niche = Niche(name="gardening")
    db_session.add(niche)
    db_session.flush()

    text = "the best gardening tips nobody tells you about this season"
    first, _ = _make_script(db_session, full_text=text, niche_id=niche.id)
    second, _ = _make_script(db_session, full_text=text, niche_id=niche.id)

    score = quality_scoring.compute_originality_score(db_session, script=second, niche_id=niche.id)
    assert score < 10.0  # near-identical text to `first` -> heavily penalized


def test_policy_risk_score_flags_banned_claims(db_session):
    script, campaign = _make_script(db_session, full_text="This is a guaranteed risk-free way to get rich quick")
    score = quality_scoring.compute_policy_risk_score(script=script, campaign=campaign)
    assert score > 0


def test_policy_risk_score_flags_missing_disclosure(db_session):
    script, campaign = _make_script(
        db_session, full_text="Check out this product, it's amazing", rules_text="Creators must disclose sponsorship."
    )
    score = quality_scoring.compute_policy_risk_score(script=script, campaign=campaign)
    assert score >= 15.0


def test_policy_risk_score_is_zero_for_clean_disclosed_script(db_session):
    script, campaign = _make_script(
        db_session, full_text="#ad Check out this great product, link in bio", rules_text="Creators must disclose sponsorship."
    )
    score = quality_scoring.compute_policy_risk_score(script=script, campaign=campaign)
    assert score == 0.0


def test_score_video_persists_quality_score_row(db_session):
    from content_factory.db.models.video import Video

    script, campaign = _make_script(db_session, full_text="a totally original video script")
    video = Video(script_id=script.id)
    db_session.add(video)
    db_session.flush()

    quality = quality_scoring.score_video(db_session, video=video, script=script, campaign=campaign, niche_id=None)

    assert quality.video_id == video.id
    assert quality.originality_score == 100.0
    assert quality.retention_prediction_score is None
    assert quality.monetization_probability_score is None


def test_auto_reject_disabled_by_default_preserves_phase1_behavior():
    """Phase 2 M2: with the default floor=0/ceiling=100, no real score can
    ever breach either threshold — Phase 1's "informational only" behavior
    must be unchanged unless an operator opts in."""
    settings = Settings()
    quality = QualityScore(video_id=1, originality_score=0.0, policy_risk_score=100.0)
    assert quality_scoring.determine_auto_reject_reason(quality, settings) is None


def test_auto_reject_fires_when_originality_floor_is_configured():
    settings = Settings(quality_originality_auto_reject_floor=30.0)
    quality = QualityScore(video_id=1, originality_score=10.0, policy_risk_score=0.0)
    reason = quality_scoring.determine_auto_reject_reason(quality, settings)
    assert reason == "auto_reject:originality_below_floor"


def test_auto_reject_fires_when_policy_risk_ceiling_is_configured():
    settings = Settings(quality_policy_risk_auto_reject_ceiling=20.0)
    quality = QualityScore(video_id=1, originality_score=100.0, policy_risk_score=50.0)
    reason = quality_scoring.determine_auto_reject_reason(quality, settings)
    assert reason == "auto_reject:policy_risk_above_ceiling"


def test_auto_reject_does_not_fire_when_scores_are_within_configured_thresholds():
    settings = Settings(quality_originality_auto_reject_floor=30.0, quality_policy_risk_auto_reject_ceiling=20.0)
    quality = QualityScore(video_id=1, originality_score=90.0, policy_risk_score=5.0)
    assert quality_scoring.determine_auto_reject_reason(quality, settings) is None
