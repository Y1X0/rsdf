"""Phase 2 M3 — Creator Account Management, exercised through the real HTTP
layer: registering an account (with token encryption configured), reading
it back without ever exposing the token, running a health check, and the
warmup graduation gate."""

from datetime import UTC, datetime, timedelta

import pytest

from content_factory.auth.jwt_service import create_access_token
from content_factory.config import get_settings


@pytest.fixture(autouse=True)
def _token_encryption_key(monkeypatch):
    """Every test in this file needs a real Fernet key configured so
    oauth_token round-trips work; cleared afterward so other test files
    (which assume no key is configured, matching Phase 1/2's zero-secrets
    default) are unaffected."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _create_account(client, **overrides) -> dict:
    payload = {"platform": "tiktok", "handle": "creator1", "daily_post_cap": 3}
    payload.update(overrides)
    return client.post("/accounts", json=payload).json()


def test_create_account_never_returns_the_oauth_token(client):
    resp = client.post(
        "/accounts",
        json={"platform": "tiktok", "handle": "creator1", "daily_post_cap": 3, "oauth_token": "super-secret-token"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_credentials"] is True
    assert "oauth_token" not in body
    assert "encrypted_oauth_token" not in body
    assert "super-secret-token" not in resp.text


def test_create_account_without_token_reports_no_credentials(client):
    account = _create_account(client)
    assert account["has_credentials"] is False
    assert account["health_tier"] == "healthy"


def test_create_account_with_malformed_encryption_key_returns_a_clear_500(client, monkeypatch):
    """Real production bug this closes: TOKEN_ENCRYPTION_KEY set to
    something that isn't a real Fernet.generate_key() value made account
    creation fail with an opaque, unactionable "Internal server error"
    (a bare, uncaught ValueError from Fernet(...) construction) instead of
    a clear message naming the actual misconfigured env var."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "not-a-real-fernet-key")
    get_settings.cache_clear()

    resp = client.post(
        "/accounts",
        json={"platform": "tiktok", "handle": "creator1", "daily_post_cap": 3, "oauth_token": "some-token"},
    )

    assert resp.status_code == 500
    assert "TOKEN_ENCRYPTION_KEY" in resp.json()["detail"]
    assert resp.json()["detail"] != "Internal server error"


def test_create_account_stores_and_returns_platform_account_id(client):
    """Required for real Instagram publishing (the IG Business Account ID,
    not derivable from `handle`) - must round-trip through create and get,
    and be included in PATCH updates."""
    account = _create_account(client, platform="instagram", platform_account_id="17841440632369231")
    assert account["platform_account_id"] == "17841440632369231"

    fetched = client.get(f"/accounts/{account['id']}").json()
    assert fetched["platform_account_id"] == "17841440632369231"

    updated = client.patch(f"/accounts/{account['id']}", json={"platform_account_id": "999888777"}).json()
    assert updated["platform_account_id"] == "999888777"


def test_create_account_without_platform_account_id_defaults_to_none(client):
    account = _create_account(client)
    assert account["platform_account_id"] is None


def test_duplicate_platform_handle_rejected(client):
    _create_account(client)
    resp = client.post("/accounts", json={"platform": "tiktok", "handle": "creator1"})
    assert resp.status_code == 409


def test_list_and_get_account(client):
    account = _create_account(client)
    resp = client.get("/accounts")
    assert resp.status_code == 200
    assert any(a["id"] == account["id"] for a in resp.json())

    resp = client.get(f"/accounts/{account['id']}")
    assert resp.status_code == 200
    assert resp.json()["handle"] == "creator1"


def test_list_accounts_respects_limit_and_offset(client):
    """Production Hardening Sprint H5: GET /accounts used to return every
    row unbounded."""
    for handle in ("creator1", "creator2", "creator3"):
        _create_account(client, handle=handle)

    first_page = client.get("/accounts", params={"limit": 2, "offset": 0}).json()
    assert len(first_page) == 2

    second_page = client.get("/accounts", params={"limit": 2, "offset": 2}).json()
    assert len(second_page) == 1
    assert {a["id"] for a in first_page}.isdisjoint({a["id"] for a in second_page})


def test_health_check_updates_account_and_persists_snapshot(client):
    account = _create_account(client)
    resp = client.post(
        f"/accounts/{account['id']}/health-check",
        json={"posting_cadence_used": 1, "engagement_trend": 0.1, "strikes_count": 0, "api_error_rate": 0.0},
    )
    assert resp.status_code == 200
    snapshot = resp.json()
    assert snapshot["health_score"] == 100.0
    assert snapshot["tier"] == "healthy"

    account_after = client.get(f"/accounts/{account['id']}").json()
    assert account_after["health_score"] == 100.0


def test_health_check_with_strikes_drops_tier(client):
    account = _create_account(client)
    resp = client.post(
        f"/accounts/{account['id']}/health-check",
        json={"posting_cadence_used": 1, "engagement_trend": 0.0, "strikes_count": 5, "api_error_rate": 0.0},
    )
    assert resp.status_code == 200
    assert resp.json()["tier"] == "restricted"


def test_warmup_graduation_rejected_for_new_account(client):
    account = _create_account(client)
    resp = client.patch(f"/accounts/{account['id']}", json={"warmup_status": "active"})
    assert resp.status_code == 422


def test_warmup_graduation_allowed_for_old_healthy_account(client):
    account = _create_account(client)

    from content_factory.db.models.account import OwnedAccount

    db = client.db_session_factory()
    try:
        row = db.get(OwnedAccount, account["id"])
        row.created_at = datetime.now(UTC) - timedelta(days=30)
        db.commit()
    finally:
        db.close()

    resp = client.patch(f"/accounts/{account['id']}", json={"warmup_status": "active"})
    assert resp.status_code == 200
    assert resp.json()["warmup_status"] == "active"


def test_active_account_cannot_be_moved_back_to_warming(client):
    account = _create_account(client)

    from content_factory.db.models.account import OwnedAccount

    db = client.db_session_factory()
    try:
        row = db.get(OwnedAccount, account["id"])
        row.created_at = datetime.now(UTC) - timedelta(days=30)
        db.commit()
    finally:
        db.close()

    client.patch(f"/accounts/{account['id']}", json={"warmup_status": "active"})
    resp = client.patch(f"/accounts/{account['id']}", json={"warmup_status": "warming"})
    assert resp.status_code == 422


def test_non_operator_cannot_create_account(client):
    settings = get_settings()
    viewer_token = create_access_token(subject="read-only-client", role="viewer", settings=settings)
    resp = client.post(
        "/accounts",
        json={"platform": "tiktok", "handle": "creator1"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


def test_account_profit_rollup_aggregates_videos_published_to_it(client):
    """Phase 2 M6: profit-per-account rolls up cost/revenue across every
    video that has been (or is scheduled to be) published to that
    account — via the publications table, not campaigns."""
    account = _create_account(client)

    campaign = client.post("/campaigns", json={"brand_name": "Acme", "cpm_rate": 3.0}).json()
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    video = client.post(f"/scripts/{scripts[0]['id']}/render", json={}).json()
    client.post(f"/videos/{video['id']}/review", json={"reviewer_id": "bob", "decision": "approved"})

    # No MEDIA_BACKUP_* configured in the test environment, so the render
    # above left a local filesystem path on Video.asset_url -
    # publishing_service now correctly refuses to publish that (see
    # test_publishing_service.py for dedicated coverage of the refusal
    # itself). This test is about the profit rollup, not the asset-hosting
    # guard, so simulate an already-publicly-reachable asset directly.
    from content_factory.db.models.video import Video

    db = client.db_session_factory()
    try:
        db.get(Video, video["id"]).asset_url = f"https://cdn.test.example/videos/{video['id']}.mp4"
        db.commit()
    finally:
        db.close()

    client.post(f"/videos/{video['id']}/publish", json={"account_id": account["id"], "title": "t", "description": "d"})

    client.post(f"/videos/{video['id']}/cost", json={"category": "human_review", "cost_usd": 3.0})
    client.post(
        f"/videos/{video['id']}/revenue",
        json={"campaign_id": campaign["id"], "approved_views": 500, "payout_realized": 15.0},
    )

    resp = client.get(f"/accounts/{account['id']}/profit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_revenue_usd"] == 15.0
    assert body["total_cost_usd"] >= 3.0
    assert body["profit_usd"] == body["total_revenue_usd"] - body["total_cost_usd"]


def test_account_profit_missing_account_returns_404(client):
    resp = client.get("/accounts/999/profit")
    assert resp.status_code == 404
