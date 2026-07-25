def _create_video(client) -> dict:
    campaign = client.post("/campaigns", json={"brand_name": "Acme", "niche_name": "fitness", "cpm_rate": 3.0}).json()
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    return client.post(f"/scripts/{scripts[0]['id']}/render", json={}).json()


def test_reject_with_reason_code(client):
    video = _create_video(client)
    resp = client.post(
        f"/videos/{video['id']}/review",
        json={"reviewer_id": "bob", "decision": "rejected", "reason_code": "off_brand_tone", "notes": "too edgy"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "rejected"
    assert body["reason_code"] == "off_brand_tone"

    updated_video = client.get(f"/videos/{video['id']}").json()
    assert updated_video["status"] == "rejected"


def test_revision_requested(client):
    video = _create_video(client)
    resp = client.post(
        f"/videos/{video['id']}/review",
        json={"reviewer_id": "bob", "decision": "revision_requested", "reason_code": "hook_weak"},
    )
    assert resp.status_code == 200
    assert client.get(f"/videos/{video['id']}").json()["status"] == "revision_requested"


def test_repeated_rejection_reason_appears_as_known_bad_pattern(client):
    video1 = _create_video(client)
    video2 = _create_video(client)

    client.post(f"/videos/{video1['id']}/review", json={"reviewer_id": "a", "decision": "rejected", "reason_code": "unverified_claim"})
    client.post(f"/videos/{video2['id']}/review", json={"reviewer_id": "a", "decision": "rejected", "reason_code": "unverified_claim"})

    patterns = client.get("/patterns").json()
    matching = [p for p in patterns if p["description"] == "repeated_rejection:unverified_claim"]
    assert len(matching) == 1
    assert matching[0]["outcome_tag"] == "known_bad"


def test_review_missing_video_returns_404(client):
    resp = client.post("/videos/999/review", json={"reviewer_id": "a", "decision": "approved"})
    assert resp.status_code == 404
