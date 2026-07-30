"""Regression tests for P1-8 (PHASE1_AUDIT.md F7 — no bounds on
cost-sensitive request fields, headlined by unbounded num_variants)."""


def _create_idea(client) -> dict:
    campaign = client.post("/campaigns", json={"brand_name": "Acme", "cpm_rate": 3.0}).json()
    return client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()


def test_num_variants_zero_is_rejected(client):
    idea = _create_idea(client)
    resp = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 0})
    assert resp.status_code == 422


def test_num_variants_above_ceiling_is_rejected(client):
    idea = _create_idea(client)
    resp = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 11})
    assert resp.status_code == 422


def test_num_variants_at_ceiling_is_accepted(client):
    idea = _create_idea(client)
    resp = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 10})
    assert resp.status_code == 200


def test_oversized_raw_notes_is_rejected(client):
    campaign = client.post("/campaigns", json={"brand_name": "Acme", "cpm_rate": 3.0}).json()
    resp = client.post(
        f"/campaigns/{campaign['id']}/research", json={"raw_notes": "x" * 20_001}
    )
    assert resp.status_code == 422


def test_negative_cpm_rate_is_rejected(client):
    resp = client.post("/campaigns", json={"brand_name": "Acme", "cpm_rate": -1.0})
    assert resp.status_code == 422


def test_oversized_brand_name_is_rejected(client):
    resp = client.post("/campaigns", json={"brand_name": "x" * 201, "cpm_rate": 3.0})
    assert resp.status_code == 422


def test_completion_rate_above_one_is_rejected(client):
    campaign = client.post("/campaigns", json={"brand_name": "Acme", "cpm_rate": 3.0}).json()
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    video = client.post(f"/scripts/{scripts[0]['id']}/render", json={}).json()

    resp = client.post(f"/videos/{video['id']}/metrics", json={"views": 100, "completion_rate": 1.5})
    assert resp.status_code == 422


def test_negative_views_is_rejected(client):
    campaign = client.post("/campaigns", json={"brand_name": "Acme", "cpm_rate": 3.0}).json()
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    video = client.post(f"/scripts/{scripts[0]['id']}/render", json={}).json()

    resp = client.post(f"/videos/{video['id']}/metrics", json={"views": -5})
    assert resp.status_code == 422


def test_negative_cost_usd_is_rejected(client):
    campaign = client.post("/campaigns", json={"brand_name": "Acme", "cpm_rate": 3.0}).json()
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    video = client.post(f"/scripts/{scripts[0]['id']}/render", json={}).json()

    resp = client.post(f"/videos/{video['id']}/cost", json={"category": "other", "cost_usd": -1.0})
    assert resp.status_code == 422
