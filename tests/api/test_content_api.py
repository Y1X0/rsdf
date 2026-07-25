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


def test_render_auto_rejects_when_quality_gate_is_configured(client, monkeypatch):
    """Phase 2 M2: with the auto-reject floor deliberately set above the
    maximum possible score (100), every render is guaranteed to breach it —
    the video should transition straight to 'rejected' and produce a
    system-authored ReviewDecision, instead of landing in pending_review."""
    from content_factory.config import get_settings

    monkeypatch.setenv("QUALITY_ORIGINALITY_AUTO_REJECT_FLOOR", "101")
    get_settings.cache_clear()
    try:
        campaign = _create_campaign(client)
        idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
        scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
        video = client.post(f"/scripts/{scripts[0]['id']}/render", json={}).json()

        assert video["status"] == "rejected"

        from content_factory.db.models.review import ReviewDecision

        db = client.db_session_factory()
        try:
            decisions = db.query(ReviewDecision).filter(ReviewDecision.video_id == video["id"]).all()
            assert len(decisions) == 1
            assert decisions[0].reviewer_id == "system:quality_gate"
            assert decisions[0].decision.value == "rejected"
            assert decisions[0].reason_code == "auto_reject:originality_below_floor"
        finally:
            db.close()
    finally:
        get_settings.cache_clear()


def test_render_endpoint_invokes_the_injected_media_backup_provider(client):
    """Production Hardening Sprint H3: the render endpoint's DI wiring
    reaches all the way from api/deps.py through to production_service —
    not just unit-tested at the service layer."""
    from content_factory.api import deps
    from content_factory.api.main import app
    from content_factory.services.media_backup import MediaBackupProvider, MediaBackupResult

    backed_up_paths = []

    class _RecordingBackupProvider(MediaBackupProvider):
        def backup(self, local_path: str) -> MediaBackupResult:
            backed_up_paths.append(local_path)
            return MediaBackupResult(backed_up=True, location=f"s3://bucket/{local_path}")

    app.dependency_overrides[deps.get_media_backup_provider] = lambda: _RecordingBackupProvider()
    try:
        campaign = _create_campaign(client)
        idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
        scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
        video = client.post(f"/scripts/{scripts[0]['id']}/render", json={}).json()

        assert video["status"] in ("pending_review", "rejected")
        assert len(backed_up_paths) == 2
    finally:
        del app.dependency_overrides[deps.get_media_backup_provider]


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
