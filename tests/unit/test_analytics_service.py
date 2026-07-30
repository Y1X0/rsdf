from datetime import UTC, datetime
from uuid import uuid4

from content_factory.db.models.agent_run import AgentRun
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.clip import Clip
from content_factory.db.models.content import ContentIdea, Script
from content_factory.db.models.enums import ClipStatus, ProcessingStatus
from content_factory.db.models.niche import Niche
from content_factory.db.models.source_video import SourceVideo
from content_factory.db.models.video import Video
from content_factory.services import analytics_service


def _make_video_with_clip(db_session, hook_text="a clip hook") -> tuple[Video, Clip]:
    niche = Niche(name=f"finance-{uuid4()}")
    db_session.add(niche)
    db_session.flush()
    campaign = Campaign(brand_name="Acme", niche_id=niche.id)
    db_session.add(campaign)
    db_session.flush()
    source_video = SourceVideo(campaign_id=campaign.id, title="Long Video", storage_path="/tmp/x.mp4")
    db_session.add(source_video)
    db_session.flush()
    clip = Clip(
        source_video_id=source_video.id, start_s=0.0, end_s=5.0, hook_text=hook_text, status=ClipStatus.RENDERED,
    )
    db_session.add(clip)
    db_session.flush()
    video = Video(clip_id=clip.id)
    db_session.add(video)
    db_session.flush()
    return video, clip


def _make_video_with_script(db_session, hook_text="a hook") -> tuple[Video, Script]:
    niche = Niche(name=f"finance-{uuid4()}")
    db_session.add(niche)
    db_session.flush()
    campaign = Campaign(brand_name="Acme", niche_id=niche.id)
    db_session.add(campaign)
    db_session.flush()
    idea = ContentIdea(campaign_id=campaign.id, concept_summary="idea")
    db_session.add(idea)
    db_session.flush()
    script = Script(
        idea_id=idea.id, variant_label="v1", hook_text=hook_text, full_text="full",
        generation_status=ProcessingStatus.COMPLETED,
    )
    db_session.add(script)
    db_session.flush()
    video = Video(script_id=script.id)
    db_session.add(video)
    db_session.flush()
    return video, script


def test_compute_viral_score_matches_specified_weights(db_session):
    video, _ = _make_video_with_script(db_session)

    # Deliberately maxed-out metrics (>= reference ceilings) so every
    # normalized term is exactly 1.0 and the total should equal 1.0.
    snapshot, score = analytics_service.record_metrics(
        db_session,
        video=video,
        views=100_000,
        avg_watch_time_s=60.0,
        completion_rate=1.0,
        shares=100,
        comments=200,
        likes=2000,
    )

    assert score.score == 1.0
    breakdown = score.breakdown_json
    assert breakdown["watch_time_norm"] == 1.0
    assert breakdown["completion_rate"] == 1.0


def test_compute_viral_score_recommendation_thresholds(db_session):
    video, _ = _make_video_with_script(db_session)
    _, low_score = analytics_service.record_metrics(db_session, video=video, views=10, completion_rate=0.05)
    assert low_score.recommendation == "retire"

    video2, _ = _make_video_with_script(db_session)
    _, high_score = analytics_service.record_metrics(
        db_session, video=video2, views=50_000, avg_watch_time_s=60, completion_rate=0.9, shares=100, comments=200, likes=2000
    )
    assert high_score.recommendation == "duplicate"


def test_record_metrics_feeds_hook_outcome_into_content_intelligence(db_session):
    video, script = _make_video_with_script(db_session, hook_text="a very specific hook")
    analytics_service.record_metrics(db_session, video=video, views=1000, completion_rate=0.5)

    from content_factory.services import content_intelligence

    hooks = content_intelligence.get_top_hooks(db_session, niche_id=video.script.idea.campaign.niche_id)
    matching = [h for h in hooks if h.hook_text == "a very specific hook"]
    assert len(matching) == 1
    assert matching[0].best_viral_score is not None


def test_record_metrics_feeds_clip_hook_outcome_into_content_intelligence_too(db_session):
    """Regression test for a real gap: clip-factory hooks never reached
    HookLibrary's outcome tracking at all before this - only Script-
    pipeline hooks did, via the `if script is not None` branch above.
    Real-world clip performance must feed the same learning loop."""
    video, clip = _make_video_with_clip(db_session, hook_text="a very specific clip hook")
    analytics_service.record_metrics(db_session, video=video, views=1000, completion_rate=0.5)

    from content_factory.services import content_intelligence

    hooks = content_intelligence.get_top_hooks(
        db_session, niche_id=clip.source_video.campaign.niche_id
    )
    matching = [h for h in hooks if h.hook_text == "a very specific clip hook"]
    assert len(matching) == 1
    assert matching[0].best_viral_score is not None


def test_record_agent_run_cost_creates_ledger_entry_from_completed_run(db_session):
    video, _ = _make_video_with_script(db_session)
    run = AgentRun(
        agent_name="script_agent",
        scope="idea.scripts",
        provider="anthropic",
        status=ProcessingStatus.COMPLETED,
        cost_usd=0.0123,
        started_at=datetime.now(UTC),
    )
    db_session.add(run)
    db_session.flush()

    entry = analytics_service.record_agent_run_cost(db_session, agent_run=run, video_id=video.id)
    assert entry is not None
    assert entry.category == "llm"
    assert float(entry.cost_usd) == 0.0123


def test_record_agent_run_cost_skips_zero_cost_runs(db_session):
    run = AgentRun(
        agent_name="script_agent", scope="idea.scripts", provider="fake",
        status=ProcessingStatus.COMPLETED, cost_usd=0.0, started_at=datetime.now(UTC),
    )
    db_session.add(run)
    db_session.flush()

    entry = analytics_service.record_agent_run_cost(db_session, agent_run=run)
    assert entry is None


def test_compute_profit_summary_nets_cost_against_revenue(db_session):
    video, _ = _make_video_with_script(db_session)
    analytics_service.record_manual_cost(db_session, video_id=video.id, category="tts", cost_usd=2.5)
    analytics_service.record_revenue(
        db_session, video=video, campaign_id=video.script.idea.campaign_id, payout_realized=10.0
    )

    summary = analytics_service.compute_profit_summary(db_session, video_id=video.id)
    assert summary["total_cost_usd"] == 2.5
    assert summary["total_revenue_usd"] == 10.0
    assert summary["profit_usd"] == 7.5


def test_dashboard_summary_reflects_created_data(db_session):
    video, _ = _make_video_with_script(db_session)
    analytics_service.record_manual_cost(db_session, video_id=video.id, category="tts", cost_usd=1.0)

    summary = analytics_service.get_dashboard_summary(db_session)
    assert summary["campaign_count"] == 1
    assert summary["total_cost_usd"] == 1.0


def test_attempt_auto_metrics_sync_not_applicable_when_not_actually_published(db_session):
    from content_factory.config import Settings
    from content_factory.db.models.account import OwnedAccount
    from content_factory.db.models.enums import AccountPlatform, PublicationStatus
    from content_factory.db.models.publication import Publication

    video, _ = _make_video_with_script(db_session)
    account = OwnedAccount(platform=AccountPlatform.TIKTOK, handle="creator1")
    db_session.add(account)
    db_session.flush()
    publication = Publication(
        video_id=video.id, account_id=account.id, platform=AccountPlatform.TIKTOK,
        title="t", description="d", hashtags=[], status=PublicationStatus.SCHEDULED,
        scheduled_at=datetime.now(UTC),
    )
    db_session.add(publication)
    db_session.flush()

    outcome = analytics_service.attempt_auto_metrics_sync(db_session, publication=publication, settings=Settings())

    assert outcome.status == "not_applicable"
    assert "nothing to sync" in outcome.detail


def test_attempt_auto_metrics_sync_not_automated_with_manual_provider(db_session):
    """Even a genuinely PUBLISHED row falls back to "not_automated" here,
    since ManualAnalyticsProvider (no real platform credentials in tests)
    always raises MetricsNotAutomated - the honest, expected default."""
    from content_factory.config import Settings
    from content_factory.db.models.account import OwnedAccount
    from content_factory.db.models.enums import AccountPlatform, PublicationStatus
    from content_factory.db.models.publication import Publication

    video, _ = _make_video_with_script(db_session)
    account = OwnedAccount(platform=AccountPlatform.TIKTOK, handle="creator1")
    db_session.add(account)
    db_session.flush()
    publication = Publication(
        video_id=video.id, account_id=account.id, platform=AccountPlatform.TIKTOK,
        title="t", description="d", hashtags=[], status=PublicationStatus.PUBLISHED,
        external_post_id="ext-123", published_at=datetime.now(UTC), scheduled_at=datetime.now(UTC),
    )
    db_session.add(publication)
    db_session.flush()

    outcome = analytics_service.attempt_auto_metrics_sync(db_session, publication=publication, settings=Settings())

    assert outcome.status == "not_automated"


def test_attempt_auto_metrics_sync_records_metrics_when_provider_succeeds(db_session, monkeypatch):
    from content_factory.analytics_ingestion.base import AnalyticsFetchResult
    from content_factory.config import Settings
    from content_factory.db.models.account import OwnedAccount
    from content_factory.db.models.enums import AccountPlatform, PublicationStatus
    from content_factory.db.models.publication import Publication

    video, _ = _make_video_with_script(db_session)
    account = OwnedAccount(platform=AccountPlatform.TIKTOK, handle="creator1")
    db_session.add(account)
    db_session.flush()
    publication = Publication(
        video_id=video.id, account_id=account.id, platform=AccountPlatform.TIKTOK,
        title="t", description="d", hashtags=[], status=PublicationStatus.PUBLISHED,
        external_post_id="ext-123", published_at=datetime.now(UTC), scheduled_at=datetime.now(UTC),
    )
    db_session.add(publication)
    db_session.flush()

    class _FakeProvider:
        def fetch_metrics(self, *, external_post_id):
            return AnalyticsFetchResult(
                views=1000, avg_watch_time_s=10.0, completion_rate=0.5, rewatch_rate=0.1,
                shares=5, comments=3, likes=50, saves=2,
            )

    import content_factory.analytics_ingestion.factory as factory_module

    monkeypatch.setattr(factory_module, "get_analytics_provider", lambda platform, settings, access_token=None: _FakeProvider())

    outcome = analytics_service.attempt_auto_metrics_sync(db_session, publication=publication, settings=Settings())

    assert outcome.status == "recorded"
    assert "views=1000" in outcome.detail
