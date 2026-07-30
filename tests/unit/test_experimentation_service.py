"""Phase 2 M6: Experimentation Engine — recommend-only, all four axes."""

from datetime import UTC, datetime

import pytest

from content_factory.db.models.account import OwnedAccount
from content_factory.db.models.analytics import MetricsSnapshot, ViralScoreRecord
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.content import ContentIdea, Script
from content_factory.db.models.enums import (
    AccountPlatform,
    ExperimentAxis,
    ExperimentStatus,
    ProcessingStatus,
    PublicationStatus,
)
from content_factory.db.models.niche import Niche
from content_factory.db.models.publication import Publication
from content_factory.db.models.video import Video
from content_factory.services import experimentation_service
from content_factory.services.experimentation_service import RecommendationNotWinner


def _make_niche(db, name="niche-a") -> Niche:
    niche = Niche(name=name)
    db.add(niche)
    db.flush()
    return niche


def _make_video_with_score(db, *, niche: Niche, hook_text: str, score: float, duration_s: float = 20.0) -> Video:
    campaign = Campaign(brand_name="Acme", niche_id=niche.id)
    db.add(campaign)
    db.flush()
    idea = ContentIdea(campaign_id=campaign.id, concept_summary="idea")
    db.add(idea)
    db.flush()
    script = Script(
        idea_id=idea.id, variant_label="v1", hook_text=hook_text, full_text="full text",
        generation_status=ProcessingStatus.COMPLETED,
    )
    db.add(script)
    db.flush()
    video = Video(script_id=script.id, duration_s=duration_s)
    db.add(video)
    db.flush()
    snapshot = MetricsSnapshot(video_id=video.id, captured_at=datetime.now(UTC), views=1000)
    db.add(snapshot)
    db.flush()
    db.add(
        ViralScoreRecord(
            metrics_snapshot_id=snapshot.id, video_id=video.id, score=score, breakdown_json={}
        )
    )
    db.flush()
    return video


def _publish(db, *, video: Video, account: OwnedAccount, published_at: datetime) -> Publication:
    pub = Publication(
        video_id=video.id, account_id=account.id, platform=account.platform, title="t", description="d",
        hashtags=[], scheduled_at=published_at, published_at=published_at, status=PublicationStatus.PUBLISHED,
    )
    db.add(pub)
    db.flush()
    return pub


def _make_account(db) -> OwnedAccount:
    account = OwnedAccount(platform=AccountPlatform.TIKTOK, handle="creator1", daily_post_cap=5)
    db.add(account)
    db.flush()
    return account


def test_hook_axis_declares_no_winner_below_min_sample_size(db_session):
    niche = _make_niche(db_session)
    for _ in range(2):
        _make_video_with_score(db_session, niche=niche, hook_text="hook A", score=0.9)
    for _ in range(2):
        _make_video_with_score(db_session, niche=niche, hook_text="hook B", score=0.1)

    experiment = experimentation_service.run_experiment(
        db_session, axis=ExperimentAxis.HOOK, niche_id=niche.id, min_sample_size=5
    )
    assert experiment.status == ExperimentStatus.INCONCLUSIVE

    from content_factory.db.models.experiment import ExperimentResult

    results = db_session.query(ExperimentResult).filter(ExperimentResult.experiment_id == experiment.id).all()
    assert len(results) == 2
    assert all(r.is_winner is False for r in results)


def test_hook_axis_declares_a_winner_when_it_clearly_beats_baseline(db_session):
    niche = _make_niche(db_session)
    for _ in range(5):
        _make_video_with_score(db_session, niche=niche, hook_text="hook A", score=0.9)
    for _ in range(5):
        _make_video_with_score(db_session, niche=niche, hook_text="hook B", score=0.1)

    experiment = experimentation_service.run_experiment(
        db_session, axis=ExperimentAxis.HOOK, niche_id=niche.id, min_sample_size=5, significance_threshold=0.1
    )
    assert experiment.status == ExperimentStatus.CONCLUDED

    from content_factory.db.models.experiment import ExperimentResult

    results = {
        r.subject_key: r
        for r in db_session.query(ExperimentResult).filter(ExperimentResult.experiment_id == experiment.id).all()
    }
    assert results["hook A"].is_winner is True
    assert results["hook B"].is_winner is False
    assert results["hook A"].sample_size == 5


def test_length_axis_buckets_by_platform_and_duration(db_session):
    niche = _make_niche(db_session)
    account = _make_account(db_session)
    now = datetime.now(UTC)

    for _ in range(5):
        video = _make_video_with_score(db_session, niche=niche, hook_text="h1", score=0.8, duration_s=10.0)
        _publish(db_session, video=video, account=account, published_at=now)
    for _ in range(5):
        video = _make_video_with_score(db_session, niche=niche, hook_text="h2", score=0.2, duration_s=45.0)
        _publish(db_session, video=video, account=account, published_at=now)

    experiment = experimentation_service.run_experiment(
        db_session, axis=ExperimentAxis.LENGTH, min_sample_size=5, significance_threshold=0.1
    )

    from content_factory.db.models.experiment import ExperimentResult

    results = {
        r.subject_key: r
        for r in db_session.query(ExperimentResult).filter(ExperimentResult.experiment_id == experiment.id).all()
    }
    assert "tiktok:0-15s" in results
    assert "tiktok:30-60s" in results
    assert results["tiktok:0-15s"].is_winner is True


def test_posting_time_axis_buckets_by_day_and_hour(db_session):
    niche = _make_niche(db_session)
    account = _make_account(db_session)

    good_time = datetime(2026, 1, 5, 18, 0, tzinfo=UTC)  # Monday 18:00
    bad_time = datetime(2026, 1, 6, 3, 0, tzinfo=UTC)  # Tuesday 03:00

    for _ in range(5):
        video = _make_video_with_score(db_session, niche=niche, hook_text="h1", score=0.9)
        _publish(db_session, video=video, account=account, published_at=good_time)
    for _ in range(5):
        video = _make_video_with_score(db_session, niche=niche, hook_text="h2", score=0.1)
        _publish(db_session, video=video, account=account, published_at=bad_time)

    experiment = experimentation_service.run_experiment(
        db_session, axis=ExperimentAxis.POSTING_TIME, min_sample_size=5, significance_threshold=0.1
    )

    from content_factory.db.models.experiment import ExperimentResult

    results = {
        r.subject_key: r
        for r in db_session.query(ExperimentResult).filter(ExperimentResult.experiment_id == experiment.id).all()
    }
    assert "tiktok:mon:18h" in results
    assert results["tiktok:mon:18h"].is_winner is True


def test_niche_axis_compares_across_niches(db_session):
    niche_a = _make_niche(db_session, name="niche-a")
    niche_b = _make_niche(db_session, name="niche-b")
    for _ in range(5):
        _make_video_with_score(db_session, niche=niche_a, hook_text="h1", score=0.9)
    for _ in range(5):
        _make_video_with_score(db_session, niche=niche_b, hook_text="h2", score=0.1)

    experiment = experimentation_service.run_experiment(
        db_session, axis=ExperimentAxis.NICHE, min_sample_size=5, significance_threshold=0.1
    )

    from content_factory.db.models.experiment import ExperimentResult

    results = {
        r.subject_key: r
        for r in db_session.query(ExperimentResult).filter(ExperimentResult.experiment_id == experiment.id).all()
    }
    assert results[str(niche_a.id)].is_winner is True
    assert results[str(niche_b.id)].is_winner is False


def test_apply_recommendation_sets_niche_allocation_weight(db_session):
    niche_a = _make_niche(db_session, name="niche-a")
    niche_b = _make_niche(db_session, name="niche-b")
    for _ in range(5):
        _make_video_with_score(db_session, niche=niche_a, hook_text="h1", score=0.9)
    for _ in range(5):
        _make_video_with_score(db_session, niche=niche_b, hook_text="h2", score=0.1)

    experiment = experimentation_service.run_experiment(
        db_session, axis=ExperimentAxis.NICHE, min_sample_size=5, significance_threshold=0.1
    )
    from content_factory.db.models.experiment import ExperimentResult

    winner = (
        db_session.query(ExperimentResult)
        .filter(ExperimentResult.experiment_id == experiment.id, ExperimentResult.is_winner.is_(True))
        .one()
    )
    experimentation_service.apply_recommendation(db_session, result=winner, applied_by="operator1")

    db_session.refresh(niche_a)
    assert niche_a.allocation_weight == experimentation_service.NICHE_WINNER_ALLOCATION_WEIGHT
    assert winner.applied_by == "operator1"
    assert winner.applied_at is not None


def test_apply_recommendation_rejects_a_non_winner(db_session):
    niche = _make_niche(db_session)
    for _ in range(2):
        _make_video_with_score(db_session, niche=niche, hook_text="hook A", score=0.5)

    experiment = experimentation_service.run_experiment(
        db_session, axis=ExperimentAxis.HOOK, niche_id=niche.id, min_sample_size=5
    )
    from content_factory.db.models.experiment import ExperimentResult

    result = db_session.query(ExperimentResult).filter(ExperimentResult.experiment_id == experiment.id).one()
    assert result.is_winner is False

    with pytest.raises(RecommendationNotWinner):
        experimentation_service.apply_recommendation(db_session, result=result, applied_by="operator1")


def test_apply_recommendation_is_idempotent(db_session):
    niche_a = _make_niche(db_session, name="niche-a")
    niche_b = _make_niche(db_session, name="niche-b")
    for _ in range(5):
        _make_video_with_score(db_session, niche=niche_a, hook_text="h1", score=0.9)
    for _ in range(5):
        _make_video_with_score(db_session, niche=niche_b, hook_text="h2", score=0.1)

    experiment = experimentation_service.run_experiment(
        db_session, axis=ExperimentAxis.NICHE, min_sample_size=5, significance_threshold=0.1
    )
    from content_factory.db.models.experiment import ExperimentResult

    winner = (
        db_session.query(ExperimentResult)
        .filter(ExperimentResult.experiment_id == experiment.id, ExperimentResult.is_winner.is_(True))
        .one()
    )
    experimentation_service.apply_recommendation(db_session, result=winner, applied_by="operator1")
    first_applied_at = winner.applied_at

    experimentation_service.apply_recommendation(db_session, result=winner, applied_by="operator2")
    assert winner.applied_at == first_applied_at
    assert winner.applied_by == "operator1"  # unchanged — the second call was a no-op
