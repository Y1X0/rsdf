"""End-to-end tests for the automated pipeline: Campaign -> Research ->
Ideas -> (human selects) -> Script -> Render -> (human reviews) -> Publish
-> Metrics, with no manual endpoint calls required between any of those
stages except the two intentional human gates (idea selection, pre-publish
review)."""


def _create_campaign(client, **overrides) -> dict:
    payload = {"brand_name": "Acme Corp", "niche_name": "personal_finance", "cpm_rate": 4.0}
    payload.update(overrides)
    return client.post("/campaigns", json=payload).json()


def test_research_automatically_generates_ideas(client):
    """Closes the Campaign -> Research -> Ideas transition: no manual
    POST /campaigns/{id}/ideas call should be needed after research."""
    campaign = _create_campaign(client)
    research_resp = client.post(
        f"/campaigns/{campaign['id']}/research", json={"raw_notes": "competitors use countdown hooks"}
    )
    assert research_resp.status_code == 200

    ideas_resp = client.get(f"/campaigns/{campaign['id']}/ideas")
    assert ideas_resp.status_code == 200
    ideas = ideas_resp.json()
    assert len(ideas) == 2  # matches conftest's canned_llm_response recommended_angles
    assert all(i["source"] == "research_agent" for i in ideas)
    assert all(i["status"] == "proposed" for i in ideas)
    assert {i["concept_summary"] for i in ideas} == {"Budgeting myths", "Quick win tips"}


def test_select_idea_cascades_through_script_and_render_automatically(client):
    """Closes the Ideas -> Script -> Render transition: after a human picks
    an idea, no manual POST /ideas/{id}/scripts or POST /scripts/{id}/render
    call is needed to reach a video awaiting review."""
    campaign = _create_campaign(client)
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "3 budgeting myths"}).json()

    resp = client.post(f"/ideas/{idea['id']}/select", json={})
    assert resp.status_code == 200
    body = resp.json()

    assert body["idea"]["status"] == "selected"
    assert body["stage_reached"] == "rendered"
    assert len(body["scripts"]) == 2  # conftest's canned_llm_response is a fixed 2-variant array
    assert body["video"] is not None
    assert body["video"]["status"] == "pending_review"
    assert body["video"]["render_status"] == "completed"
    assert body["video"]["script_id"] == body["scripts"][0]["id"]

    # The rendered video is a completely ordinary Video row - the existing,
    # unmodified GET /videos/{id} and review endpoints work on it unchanged.
    get_resp = client.get(f"/videos/{body['video']['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["quality_score"] is not None


def test_select_idea_is_idempotent_and_does_not_regenerate_scripts(client):
    campaign = _create_campaign(client)
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()

    first = client.post(f"/ideas/{idea['id']}/select", json={})
    second = client.post(f"/ideas/{idea['id']}/select", json={})

    assert first.json()["video"]["id"] == second.json()["video"]["id"]
    assert first.json()["scripts"][0]["id"] == second.json()["scripts"][0]["id"]


def test_select_idea_404_when_missing(client):
    resp = client.post("/ideas/999999/select", json={})
    assert resp.status_code == 404


def test_reject_idea_then_select_is_blocked(client):
    campaign = _create_campaign(client)
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()

    reject_resp = client.post(f"/ideas/{idea['id']}/reject")
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"

    select_resp = client.post(f"/ideas/{idea['id']}/select", json={})
    assert select_resp.status_code == 409


def _select_and_get_video(client, campaign_id: int) -> dict:
    idea = client.post(f"/campaigns/{campaign_id}/ideas", json={"concept_summary": "idea"}).json()
    result = client.post(f"/ideas/{idea['id']}/select", json={}).json()
    return result["video"]


def test_review_approval_auto_publish_skipped_when_no_eligible_account(client):
    """Closes the Review -> Publish transition mechanically (no manual
    POST /videos/{id}/publish call needed), but honestly reports a clear
    reason instead of a fabricated success when there's no account to
    publish to - a real external precondition, not a code bug."""
    campaign = _create_campaign(client)
    video = _select_and_get_video(client, campaign["id"])

    resp = client.post(f"/videos/{video['id']}/review", json={"decision": "approved"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["decision"] == "approved"
    assert body["auto_publish_status"] == "skipped"
    assert "no eligible" in body["auto_publish_detail"]
    assert body["auto_metrics_status"] is None  # never attempted - nothing was published


def test_review_approval_auto_publishes_when_exactly_one_eligible_account(client):
    """The honest default outcome with the safe ManualPublishingProvider:
    "scheduled", not "published" - a human still has to actually post it,
    but the mechanical Publication row and cascade both ran with zero
    manual endpoint calls."""
    campaign = _create_campaign(client)
    account = client.post("/accounts", json={"platform": "tiktok", "handle": "@acme_test"}).json()
    video = _select_and_get_video(client, campaign["id"])

    resp = client.post(f"/videos/{video['id']}/review", json={"decision": "approved"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["auto_publish_status"] == "scheduled"
    assert f"account #{account['id']}" in body["auto_publish_detail"]
    assert body["auto_metrics_status"] == "not_applicable"

    publications = client.get("/publications").json()
    assert any(p["video_id"] == video["id"] for p in publications)


def test_review_approval_auto_publish_skipped_when_multiple_ambiguous_accounts(client):
    """Two eligible accounts, neither an unambiguous niche match: must not
    guess which one gets real content published to it."""
    campaign = _create_campaign(client)
    client.post("/accounts", json={"platform": "tiktok", "handle": "@one"})
    client.post("/accounts", json={"platform": "youtube", "handle": "@two"})
    video = _select_and_get_video(client, campaign["id"])

    resp = client.post(f"/videos/{video['id']}/review", json={"decision": "approved"})
    body = resp.json()

    assert body["auto_publish_status"] == "skipped"
    assert "ambiguous" in body["auto_publish_detail"]


def test_review_rejection_does_not_attempt_auto_publish(client):
    campaign = _create_campaign(client)
    client.post("/accounts", json={"platform": "tiktok", "handle": "@acme_test"})
    video = _select_and_get_video(client, campaign["id"])

    resp = client.post(
        f"/videos/{video['id']}/review", json={"decision": "rejected", "reason_code": "policy_risk"}
    )
    body = resp.json()

    assert body["decision"] == "rejected"
    assert body["auto_publish_status"] is None
    assert body["auto_publish_detail"] is None

    publications = client.get("/publications").json()
    assert not any(p["video_id"] == video["id"] for p in publications)
