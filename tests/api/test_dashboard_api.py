def test_dashboard_summary_reflects_pipeline_state(client):
    resp = client.get("/dashboard/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_count"] == 0
    assert body["pending_review_count"] == 0

    campaign = client.post("/campaigns", json={"brand_name": "Acme", "cpm_rate": 3.0}).json()
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    client.post(f"/scripts/{scripts[0]['id']}/render", json={})

    summary = client.get("/dashboard/summary").json()
    assert summary["campaign_count"] == 1
    assert summary["pending_review_count"] == 1
    assert summary["video_counts_by_status"].get("pending_review") == 1


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
