"""Regression tests for P1-4 (PHASE1_AUDIT.md F3 — "Niche management is a
dead end"): niches now have a real read/write API, and Campaign
Intelligence scoring derives a real internal signal instead of a hardcoded
0.5 when no manual value is set."""


def test_create_list_get_update_niche(client):
    create_resp = client.post("/niches", json={"name": "personal_finance", "category": "finance"})
    assert create_resp.status_code == 200
    niche = create_resp.json()
    assert niche["saturation_score"] is None

    list_resp = client.get("/niches")
    assert any(n["id"] == niche["id"] for n in list_resp.json())

    get_resp = client.get(f"/niches/{niche['id']}")
    assert get_resp.status_code == 200

    update_resp = client.patch(f"/niches/{niche['id']}", json={"saturation_score": 0.3, "trend_score": 0.7})
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["saturation_score"] == 0.3
    assert updated["trend_score"] == 0.7
    # Fields not included in the PATCH body are left unchanged.
    assert updated["category"] == "finance"


def test_create_niche_conflicts_on_duplicate_name(client):
    client.post("/niches", json={"name": "gaming"})
    resp = client.post("/niches", json={"name": "gaming"})
    assert resp.status_code == 409


def test_update_missing_niche_returns_404(client):
    resp = client.patch("/niches/999", json={"saturation_score": 0.5})
    assert resp.status_code == 404


def test_campaign_scoring_uses_manual_niche_values_when_set(client):
    niche = client.post(
        "/niches", json={"name": "manual_niche", "saturation_score": 0.1, "trend_score": 0.9}
    ).json()
    campaign = client.post(
        "/campaigns", json={"brand_name": "Acme", "niche_name": "manual_niche", "cpm_rate": 4.0}
    ).json()
    assert campaign["niche_id"] == niche["id"]

    score = client.post(f"/campaigns/{campaign['id']}/score").json()
    assert score["competition_level"] == 0.1
    assert score["niche_fit_score"] == 0.9
    assert score["breakdown_json"]["competition_source"] == "manual"
    assert score["breakdown_json"]["niche_fit_source"] == "manual"


def test_campaign_scoring_derives_competition_from_internal_campaign_count(client):
    """No manual saturation_score set — with several other campaigns
    already in the niche, competition_level should reflect that real
    internal signal rather than falling back to the neutral 0.5 default.
    Deliberately picks a campaign count (3) whose derived value (0.3)
    differs from the 0.5 neutral fallback, so the numeric assertion alone
    (not just the source tag) proves this isn't the fallback path."""
    for i in range(3):
        client.post("/campaigns", json={"brand_name": f"Brand {i}", "niche_name": "busy_niche", "cpm_rate": 2.0})

    campaign = client.post(
        "/campaigns", json={"brand_name": "Newcomer", "niche_name": "busy_niche", "cpm_rate": 2.0}
    ).json()

    score = client.post(f"/campaigns/{campaign['id']}/score").json()
    assert score["breakdown_json"]["competition_source"] == "internal_campaign_count"
    assert score["competition_level"] == 0.3  # 3 other campaigns / threshold of 10


def test_niche_profit_rollup_aggregates_across_its_campaigns(client):
    """Phase 2 M6: profit-per-niche rolls up cost_ledger/revenue_snapshots
    across every campaign in the niche, not just one video."""
    niche = client.post("/niches", json={"name": "profit_niche"}).json()
    campaign = client.post(
        "/campaigns", json={"brand_name": "Acme", "niche_name": "profit_niche", "cpm_rate": 3.0}
    ).json()
    assert campaign["niche_id"] == niche["id"]

    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    video = client.post(f"/scripts/{scripts[0]['id']}/render", json={}).json()

    client.post(f"/videos/{video['id']}/cost", json={"category": "human_review", "cost_usd": 2.0})
    client.post(
        f"/videos/{video['id']}/revenue",
        json={"campaign_id": campaign["id"], "approved_views": 1000, "payout_realized": 10.0},
    )

    resp = client.get(f"/niches/{niche['id']}/profit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_revenue_usd"] == 10.0
    assert body["total_cost_usd"] >= 2.0
    assert body["profit_usd"] == body["total_revenue_usd"] - body["total_cost_usd"]


def test_niche_profit_missing_niche_returns_404(client):
    resp = client.get("/niches/999/profit")
    assert resp.status_code == 404


def test_campaign_scoring_falls_back_to_neutral_only_when_truly_no_data(client):
    campaign = client.post(
        "/campaigns", json={"brand_name": "Solo", "niche_name": "brand_new_empty_niche", "cpm_rate": 2.0}
    ).json()

    score = client.post(f"/campaigns/{campaign['id']}/score").json()
    assert score["competition_level"] == 0.5
    assert score["niche_fit_score"] == 0.5
    assert score["breakdown_json"]["competition_source"] == "insufficient_data"
    assert score["breakdown_json"]["niche_fit_source"] == "insufficient_data"
