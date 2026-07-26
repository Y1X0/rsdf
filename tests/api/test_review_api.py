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


def test_list_videos_respects_limit_and_offset(client):
    """Production Hardening Sprint H5: GET /videos used to return every
    row unbounded."""
    for _ in range(3):
        _create_video(client)

    first_page = client.get("/videos", params={"limit": 2, "offset": 0}).json()
    assert len(first_page) == 2

    second_page = client.get("/videos", params={"limit": 2, "offset": 2}).json()
    assert len(second_page) == 1
    assert {v["id"] for v in first_page}.isdisjoint({v["id"] for v in second_page})


def test_list_pending_review_respects_limit_and_offset(client):
    """Production Hardening Sprint H5: GET /videos/pending-review used to
    return every row unbounded."""
    for _ in range(3):
        _create_video(client)

    first_page = client.get("/videos/pending-review", params={"limit": 2, "offset": 0}).json()
    assert len(first_page) == 2

    second_page = client.get("/videos/pending-review", params={"limit": 2, "offset": 2}).json()
    assert len(second_page) == 1
    assert {v["id"] for v in first_page}.isdisjoint({v["id"] for v in second_page})
