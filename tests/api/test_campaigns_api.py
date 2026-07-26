def test_create_campaign_and_fetch_it(client):
    payload = {
        "brand_name": "Acme Corp",
        "niche_name": "personal_finance",
        "cpm_rate": 4.0,
        "budget_cap": 5000,
        "rules_text": "Tag @acme in the caption.",
    }
    resp = client.post("/campaigns", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["brand_name"] == "Acme Corp"
    assert body["niche_id"] is not None
    campaign_id = body["id"]

    get_resp = client.get(f"/campaigns/{campaign_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == campaign_id


def test_list_campaigns_respects_limit_and_offset(client):
    """Production Hardening Sprint H5: GET /campaigns used to return every
    row unbounded."""
    for brand in ("Acme A", "Acme B", "Acme C"):
        client.post("/campaigns", json={"brand_name": brand, "cpm_rate": 4.0})

    first_page = client.get("/campaigns", params={"limit": 2, "offset": 0}).json()
    assert len(first_page) == 2

    second_page = client.get("/campaigns", params={"limit": 2, "offset": 2}).json()
    assert len(second_page) == 1
    assert {c["id"] for c in first_page}.isdisjoint({c["id"] for c in second_page})


def test_create_campaign_is_idempotent_on_repeated_identical_request(client):
    payload = {"brand_name": "Acme Corp", "cpm_rate": 4.0, "idempotency_key": "same-key"}
    first = client.post("/campaigns", json=payload)
    second = client.post("/campaigns", json=payload)

    assert first.json()["id"] == second.json()["id"]
    assert len(client.get("/campaigns").json()) == 1


def test_create_campaign_conflicts_on_key_reuse_with_different_payload(client):
    client.post("/campaigns", json={"brand_name": "Acme", "cpm_rate": 4.0, "idempotency_key": "dup-key"})
    resp = client.post("/campaigns", json={"brand_name": "Different Brand", "cpm_rate": 4.0, "idempotency_key": "dup-key"})
    assert resp.status_code == 409


def test_get_missing_campaign_returns_404(client):
    resp = client.get("/campaigns/999")
    assert resp.status_code == 404


def test_score_campaign_endpoint_returns_composite_score(client):
    create_resp = client.post("/campaigns", json={"brand_name": "Acme", "cpm_rate": 5.0})
    campaign_id = create_resp.json()["id"]

    score_resp = client.post(f"/campaigns/{campaign_id}/score")
    assert score_resp.status_code == 200
    body = score_resp.json()
    assert body["campaign_id"] == campaign_id
    assert body["composite_score"] is not None
    assert body["recommendation"] in {"proceed", "test_batch_only", "reject"}

    # Re-scoring appends a new row rather than overwriting.
    client.post(f"/campaigns/{campaign_id}/score")
    campaign = client.get(f"/campaigns/{campaign_id}").json()
    assert campaign["latest_score"] is not None
