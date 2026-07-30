from content_factory.db.models.campaign import Campaign
from content_factory.db.models.niche import Niche
from content_factory.services.campaign_scoring import score_campaign


def test_high_cpm_low_saturation_scores_well(db_session):
    niche = Niche(name="personal_finance", saturation_score=0.2, trend_score=0.8)
    db_session.add(niche)
    db_session.flush()

    campaign = Campaign(brand_name="Acme Corp", niche_id=niche.id, cpm_rate=5.0, rules_text="Tag us in the caption.")
    db_session.add(campaign)
    db_session.flush()

    score = score_campaign(db_session, campaign=campaign)

    assert score.composite_score > 0.5
    assert score.recommendation in {"proceed", "test_batch_only"}
    assert score.expected_roi_low is not None
    assert score.expected_roi_high >= score.expected_roi_median >= score.expected_roi_low


def test_low_cpm_high_saturation_scores_poorly(db_session):
    niche = Niche(name="saturated_niche", saturation_score=0.95, trend_score=0.1)
    db_session.add(niche)
    db_session.flush()

    campaign = Campaign(brand_name="Acme Corp", niche_id=niche.id, cpm_rate=0.1)
    db_session.add(campaign)
    db_session.flush()

    score = score_campaign(db_session, campaign=campaign)

    assert score.recommendation == "reject"


def test_restrictive_rules_increase_difficulty_score(db_session):
    easy = Campaign(brand_name="Acme", cpm_rate=2.0, rules_text="Have fun with it.")
    hard = Campaign(
        brand_name="Acme",
        cpm_rate=2.0,
        rules_text="Exclusive partnership. No AI generated content. Must be a verified account with minimum follower count. Organic only.",
    )
    db_session.add_all([easy, hard])
    db_session.flush()

    easy_score = score_campaign(db_session, campaign=easy)
    hard_score = score_campaign(db_session, campaign=hard)

    assert hard_score.difficulty_score > easy_score.difficulty_score


def test_missing_niche_data_defaults_to_neutral(db_session):
    campaign = Campaign(brand_name="Acme", cpm_rate=3.0)
    db_session.add(campaign)
    db_session.flush()

    score = score_campaign(db_session, campaign=campaign)

    assert score.competition_level == 0.5
    assert score.niche_fit_score == 0.5


def test_negative_pessimistic_roi_forces_reject_even_with_good_composite(db_session):
    niche = Niche(name="great_niche", saturation_score=0.0, trend_score=1.0)
    db_session.add(niche)
    db_session.flush()

    # Very low CPM means even the "low" view scenario is a loss once
    # production cost is subtracted, despite a great niche/competition profile.
    campaign = Campaign(brand_name="Acme", niche_id=niche.id, cpm_rate=0.05)
    db_session.add(campaign)
    db_session.flush()

    score = score_campaign(db_session, campaign=campaign)

    assert score.expected_roi_low < 0
    assert score.recommendation != "proceed"
