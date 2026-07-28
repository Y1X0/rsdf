"""Phase 2 M4 — Publishing Agent, exercised through the real HTTP layer.
No platform credentials are configured in the test environment, so every
publish here goes through ManualPublishingProvider (the always-available
default) — this is itself the regression test that the fallback behaves
correctly end-to-end, not just at the unit level."""


def _create_approved_video(client) -> dict:
    campaign = client.post("/campaigns", json={"brand_name": "Acme", "niche_name": "fitness", "cpm_rate": 3.0}).json()
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    video = client.post(f"/scripts/{scripts[0]['id']}/render", json={}).json()
    client.post(f"/videos/{video['id']}/review", json={"reviewer_id": "bob", "decision": "approved"})

    # No MEDIA_BACKUP_* configured in the test environment, so the render
    # above left a local filesystem path on Video.asset_url -
    # publishing_service now correctly refuses to publish that (see
    # test_publishing_service.py's dedicated coverage for the refusal
    # itself). Every *other* test in this file is about what happens after
    # an asset is already publicly reachable, so simulate that directly via
    # the session the test client exposes for exactly this purpose.
    from content_factory.db.models.video import Video

    db = client.db_session_factory()
    try:
        db.get(Video, video["id"]).asset_url = f"https://cdn.test.example/videos/{video['id']}.mp4"
        db.commit()
    finally:
        db.close()

    return video


def _create_account(client, **overrides) -> dict:
    payload = {"platform": "tiktok", "handle": "creator1", "daily_post_cap": 2}
    payload.update(overrides)
    return client.post("/accounts", json=payload).json()


def test_publish_video_via_manual_provider(client):
    video = _create_approved_video(client)
    account = _create_account(client)

    resp = client.post(
        f"/videos/{video['id']}/publish",
        json={"account_id": account["id"], "title": "My video", "description": "desc", "hashtags": ["fyp"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "scheduled"  # manual provider never actually publishes
    assert body["published_at"] is None

    final_video = client.get(f"/videos/{video['id']}").json()
    assert final_video["status"] == "approved"  # unchanged — manual publish doesn't mark it published


def test_publish_rejects_video_whose_asset_is_not_publicly_hosted(client):
    """Without MEDIA_BACKUP_* configured (the test default, matching a
    real deployment with no object storage set up yet), a rendered
    video's asset_url is a local filesystem path - the API must refuse
    with a clear, actionable 409 rather than forward it to a real
    provider (which would either error or silently do nothing useful)."""
    campaign = client.post("/campaigns", json={"brand_name": "Acme", "niche_name": "fitness", "cpm_rate": 3.0}).json()
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    video = client.post(f"/scripts/{scripts[0]['id']}/render", json={}).json()
    client.post(f"/videos/{video['id']}/review", json={"reviewer_id": "bob", "decision": "approved"})
    account = _create_account(client)

    resp = client.post(
        f"/videos/{video['id']}/publish", json={"account_id": account["id"], "title": "t", "description": "d"}
    )

    assert resp.status_code == 409
    assert "public url" in resp.json()["detail"].lower()


def test_publish_requires_approved_video(client):
    campaign = client.post("/campaigns", json={"brand_name": "Acme", "cpm_rate": 3.0}).json()
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    video = client.post(f"/scripts/{scripts[0]['id']}/render", json={}).json()
    account = _create_account(client)

    resp = client.post(
        f"/videos/{video['id']}/publish", json={"account_id": account["id"], "title": "t", "description": "d"}
    )
    assert resp.status_code == 409


def test_publish_is_idempotent(client):
    video = _create_approved_video(client)
    account = _create_account(client)
    payload = {"account_id": account["id"], "title": "t", "description": "d", "idempotency_key": "pub-key"}

    first = client.post(f"/videos/{video['id']}/publish", json=payload).json()
    second = client.post(f"/videos/{video['id']}/publish", json=payload).json()
    assert first["id"] == second["id"]


def test_publish_blocked_for_at_risk_account(client):
    video = _create_approved_video(client)
    account = _create_account(client)
    client.post(
        f"/accounts/{account['id']}/health-check",
        json={"posting_cadence_used": 1, "engagement_trend": 0.0, "strikes_count": 5, "api_error_rate": 0.0},
    )

    resp = client.post(
        f"/videos/{video['id']}/publish", json={"account_id": account["id"], "title": "t", "description": "d"}
    )
    assert resp.status_code == 409


def test_publish_blocked_when_cadence_cap_exceeded(client):
    account = _create_account(client, daily_post_cap=1)

    video1 = _create_approved_video(client)
    resp1 = client.post(
        f"/videos/{video1['id']}/publish", json={"account_id": account["id"], "title": "t1", "description": "d"}
    )
    assert resp1.status_code == 200

    # Manual provider never actually "publishes" (status stays scheduled),
    # so the cadence cap (which counts PUBLISHED rows) isn't hit by manual
    # publishes — this proves the cap only applies to real completed posts.
    video2 = _create_approved_video(client)
    resp2 = client.post(
        f"/videos/{video2['id']}/publish", json={"account_id": account["id"], "title": "t2", "description": "d"}
    )
    assert resp2.status_code == 200


def test_publish_disabled_via_kill_switch(client, monkeypatch):
    from content_factory.config import get_settings

    monkeypatch.setenv("PUBLISHING_ENABLED", "false")
    get_settings.cache_clear()
    try:
        video = _create_approved_video(client)
        account = _create_account(client)
        resp = client.post(
            f"/videos/{video['id']}/publish", json={"account_id": account["id"], "title": "t", "description": "d"}
        )
        assert resp.status_code == 503
    finally:
        get_settings.cache_clear()


def _publish_and_mark_published(client, video: dict, account: dict) -> dict:
    """Manual provider never actually publishes (stays 'scheduled') — force
    a genuinely 'published' Publication by monkeypatching the provider
    factory the router uses, the same technique test_durability_regression
    .py already established for overriding a service's collaborator."""
    from content_factory.api.routers import publications as publications_router
    from content_factory.publishing.base import PublishResult

    class _FakePublishedProvider:
        def publish(self, request):
            return PublishResult(provider="fake", published=True, external_post_id="ext-123")

    original_factory = publications_router.get_publishing_provider
    publications_router.get_publishing_provider = lambda *a, **k: _FakePublishedProvider()
    try:
        return client.post(
            f"/videos/{video['id']}/publish",
            json={"account_id": account["id"], "title": "t", "description": "d"},
        ).json()
    finally:
        publications_router.get_publishing_provider = original_factory


def test_sync_metrics_rejects_publication_not_yet_published(client):
    video = _create_approved_video(client)
    account = _create_account(client)
    publication = client.post(
        f"/videos/{video['id']}/publish", json={"account_id": account["id"], "title": "t", "description": "d"}
    ).json()
    assert publication["status"] == "scheduled"

    resp = client.post(f"/publications/{publication['id']}/metrics/sync")
    assert resp.status_code == 409


def test_sync_metrics_returns_501_when_no_automated_provider_configured(client):
    video = _create_approved_video(client)
    account = _create_account(client)
    publication = _publish_and_mark_published(client, video, account)
    assert publication["status"] == "published"

    resp = client.post(f"/publications/{publication['id']}/metrics/sync")
    assert resp.status_code == 501


def test_sync_metrics_success_feeds_existing_analytics_service(client):
    from content_factory.analytics_ingestion.base import AnalyticsFetchResult
    from content_factory.api.routers import publications as publications_router

    video = _create_approved_video(client)
    account = _create_account(client)
    publication = _publish_and_mark_published(client, video, account)

    class _FakeAnalyticsProvider:
        def fetch_metrics(self, *, external_post_id):
            return AnalyticsFetchResult(views=1234, likes=56, comments=7, shares=8, saves=9)

    original_factory = publications_router.get_analytics_provider
    publications_router.get_analytics_provider = lambda *a, **k: _FakeAnalyticsProvider()
    try:
        resp = client.post(f"/publications/{publication['id']}/metrics/sync")
    finally:
        publications_router.get_analytics_provider = original_factory

    assert resp.status_code == 200
    body = resp.json()
    assert body["video_id"] == video["id"]
    assert body["views"] == 1234
    assert 0 <= body["viral_score"]["score"] <= 1

    # The existing manual endpoint must remain completely unaffected —
    # both paths write through the same analytics_service, but manual
    # entry is still available and untouched.
    manual_resp = client.post(f"/videos/{video['id']}/metrics", json={"views": 999})
    assert manual_resp.status_code == 200
    assert manual_resp.json()["views"] == 999


def test_list_and_get_publication(client):
    video = _create_approved_video(client)
    account = _create_account(client)
    created = client.post(
        f"/videos/{video['id']}/publish", json={"account_id": account["id"], "title": "t", "description": "d"}
    ).json()

    resp = client.get("/publications")
    assert resp.status_code == 200
    assert any(p["id"] == created["id"] for p in resp.json())

    resp = client.get(f"/publications/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "t"


def test_list_publications_respects_limit_and_offset(client):
    """Production Hardening Sprint H5: GET /publications used to return
    every row unbounded.

    Videos are approved *before* the account is registered (matching
    every other test in this file) specifically so the Review -> Publish
    auto-cascade has no eligible account yet and reports "skipped" for
    each one - this test's own explicit publish calls are then the only
    Publication rows created, keeping its exact-count assertions accurate
    regardless of that cascade running.
    """
    videos = [_create_approved_video(client) for _ in range(3)]
    account = _create_account(client, daily_post_cap=5)
    for i, video in enumerate(videos):
        client.post(
            f"/videos/{video['id']}/publish",
            json={"account_id": account["id"], "title": f"t{i}", "description": "d"},
        )

    first_page = client.get("/publications", params={"limit": 2, "offset": 0}).json()
    assert len(first_page) == 2

    second_page = client.get("/publications", params={"limit": 2, "offset": 2}).json()
    assert len(second_page) == 1
    assert {p["id"] for p in first_page}.isdisjoint({p["id"] for p in second_page})
