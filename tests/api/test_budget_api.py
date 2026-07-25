"""Phase 2 M1 — active Cost Control Layer, exercised through the real HTTP
layer: setting a ceiling, reading status, and the governor actually
blocking (402) a cost-incurring endpoint once a ceiling is met, with an
alert delivered through the injected NotificationProvider."""

from content_factory.auth.jwt_service import create_access_token
from content_factory.config import get_settings


def _create_campaign(client, niche_name="budget-api-niche") -> dict:
    return client.post(
        "/campaigns", json={"brand_name": "Acme", "niche_name": niche_name, "cpm_rate": 3.0}
    ).json()


def test_operator_can_create_and_list_ceilings(client):
    resp = client.post("/budget/ceilings", json={"scope": "system", "monthly_limit_usd": 500.0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"] == "system"
    assert body["monthly_limit_usd"] == 500.0
    assert body["last_alert_threshold_pct"] is None

    resp = client.get("/budget/ceilings")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_non_operator_cannot_create_ceiling(client):
    settings = get_settings()
    viewer_token = create_access_token(subject="read-only-client", role="viewer", settings=settings)
    resp = client.post(
        "/budget/ceilings",
        json={"scope": "system", "monthly_limit_usd": 500.0},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


def test_ceiling_creation_rejects_non_positive_limit(client):
    resp = client.post("/budget/ceilings", json={"scope": "system", "monthly_limit_usd": 0})
    assert resp.status_code == 422


def test_budget_status_reports_zero_spend_with_no_activity(client):
    client.post("/budget/ceilings", json={"scope": "system", "monthly_limit_usd": 100.0})
    resp = client.get("/budget/status")
    assert resp.status_code == 200
    statuses = resp.json()
    assert len(statuses) == 1
    assert statuses[0]["spend_usd"] == 0.0
    assert statuses[0]["is_blocked"] is False


def test_research_endpoint_blocked_once_ceiling_is_met(client, fake_notification_provider):
    campaign = _create_campaign(client)

    # A ceiling of $0 means any recorded spend is already "at" the ceiling —
    # simplest way to deterministically force is_blocked without needing to
    # rely on the fake LLM/TTS providers' exact (zero) cost.
    client.post("/budget/ceilings", json={"scope": "system", "monthly_limit_usd": 0.01})

    # First research call: spend so far is 0, so 0/0.01 = 0% — allowed.
    resp = client.post(f"/campaigns/{campaign['id']}/research", json={"raw_notes": "notes"})
    assert resp.status_code == 200

    # Manually push the ledger over the ceiling the same way a real paid
    # provider call would, then confirm the next cost-incurring call is
    # rejected with 402 rather than proceeding.
    from content_factory.db.models.analytics import CostLedger
    from datetime import UTC, datetime

    db = client.db_session_factory()
    try:
        db.add(
            CostLedger(
                campaign_id=campaign["id"],
                category="llm",
                provider="test",
                cost_usd=5.0,
                recorded_at=datetime.now(UTC),
            )
        )
        db.commit()
    finally:
        db.close()

    resp = client.post(f"/campaigns/{campaign['id']}/research", json={"raw_notes": "more notes"})
    assert resp.status_code == 402
    assert "budget" in resp.json()["detail"].lower()

    # The 100% threshold must have fired exactly one alert through the
    # injected notification provider.
    assert len(fake_notification_provider.sent) == 1
    assert fake_notification_provider.sent[0].severity.value == "critical"
