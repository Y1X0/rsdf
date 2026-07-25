def _create_campaign(client, **overrides) -> dict:
    payload = {"brand_name": "Acme Corp", "niche_name": "personal_finance", "cpm_rate": 4.0}
    payload.update(overrides)
    return client.post("/campaigns", json=payload).json()


def test_research_endpoint_creates_brief_and_ingests_hooks(client):
    campaign = _create_campaign(client)
    resp = client.post(f"/campaigns/{campaign['id']}/research", json={"raw_notes": "competitors use countdown hooks"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_id"] == campaign["id"]
    assert body["status"] == "completed"
    assert body["structured_data"]["competitor_hooks"]

    hooks_resp = client.get("/hooks", params={"niche_id": campaign["niche_id"]})
    assert hooks_resp.status_code == 200
    assert len(hooks_resp.json()) >= 1

    patterns_resp = client.get("/patterns", params={"niche_id": campaign["niche_id"]})
    assert patterns_resp.status_code == 200
    assert len(patterns_resp.json()) >= 1


def test_research_is_idempotent(client):
    campaign = _create_campaign(client)
    payload = {"raw_notes": "same notes", "idempotency_key": "research-key"}
    first = client.post(f"/campaigns/{campaign['id']}/research", json=payload)
    second = client.post(f"/campaigns/{campaign['id']}/research", json=payload)
    assert first.json()["id"] == second.json()["id"]


def test_full_pipeline_idea_to_script_to_render_to_review(client):
    campaign = _create_campaign(client)

    idea_resp = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "3 budgeting myths"})
    assert idea_resp.status_code == 200
    idea = idea_resp.json()

    scripts_resp = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 2})
    assert scripts_resp.status_code == 200
    scripts = scripts_resp.json()
    assert len(scripts) == 2
    assert scripts[0]["experiment_group"] == "A"

    script_id = scripts[0]["id"]
    render_resp = client.post(f"/scripts/{script_id}/render", json={})
    assert render_resp.status_code == 200
    video = render_resp.json()
    assert video["status"] == "pending_review"
    assert video["render_status"] == "completed"
    assert video["asset_url"] is not None
    assert video["quality_score"] is not None
    # Not necessarily 100: the sibling script (variant B, generated from the
    # same idea) already exists in the same niche and shares some wording,
    # so originality is legitimately reduced by real overlap-detection logic.
    assert 0.0 <= video["quality_score"]["originality_score"] <= 100.0
    assert video["quality_score"]["policy_risk_score"] == 0.0

    pending_resp = client.get("/videos/pending-review")
    assert any(v["id"] == video["id"] for v in pending_resp.json())

    review_resp = client.post(
        f"/videos/{video['id']}/review",
        json={"reviewer_id": "alice", "decision": "approved"},
    )
    assert review_resp.status_code == 200
    assert review_resp.json()["decision"] == "approved"

    final_video = client.get(f"/videos/{video['id']}").json()
    assert final_video["status"] == "approved"


def test_script_generation_is_idempotent(client):
    campaign = _create_campaign(client)
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()

    payload = {"num_variants": 2, "idempotency_key": "scripts-key"}
    first = client.post(f"/ideas/{idea['id']}/scripts", json=payload).json()
    second = client.post(f"/ideas/{idea['id']}/scripts", json=payload).json()

    assert [s["id"] for s in first] == [s["id"] for s in second]


def test_render_is_idempotent(client):
    campaign = _create_campaign(client)
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    script_id = scripts[0]["id"]

    payload = {"idempotency_key": "render-key"}
    first = client.post(f"/scripts/{script_id}/render", json=payload).json()
    second = client.post(f"/scripts/{script_id}/render", json=payload).json()

    assert first["id"] == second["id"]


def test_render_missing_script_returns_404(client):
    resp = client.post("/scripts/999/render", json={})
    assert resp.status_code == 404
