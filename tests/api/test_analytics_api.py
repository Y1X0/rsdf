def _create_video(client) -> dict:
    campaign = client.post("/campaigns", json={"brand_name": "Acme", "niche_name": "fitness", "cpm_rate": 3.0}).json()
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    video = client.post(f"/scripts/{scripts[0]['id']}/render", json={}).json()
    return {**video, "campaign_id": campaign["id"]}


def test_submit_metrics_returns_viral_score(client):
    video = _create_video(client)
    resp = client.post(
        f"/videos/{video['id']}/metrics",
        json={"views": 10000, "avg_watch_time_s": 30, "completion_rate": 0.6, "shares": 25, "comments": 50, "likes": 500},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["views"] == 10000
    assert 0 <= body["viral_score"]["score"] <= 1
    assert body["viral_score"]["recommendation"] in {"duplicate", "iterate", "retire"}


def test_submit_cost_and_revenue_feed_profit_summary(client):
    video = _create_video(client)
    client.post(f"/videos/{video['id']}/cost", json={"category": "human_review", "cost_usd": 1.5, "note": "reviewer time"})
    client.post(
        f"/videos/{video['id']}/revenue",
        json={"campaign_id": video["campaign_id"], "approved_views": 5000, "payout_realized": 20.0, "status": "paid"},
    )

    profit = client.get(f"/videos/{video['id']}/profit").json()
    assert profit["total_revenue_usd"] == 20.0
    # cost includes the manual entry plus whatever the TTS/render agent runs cost (likely 0 with fakes)
    assert profit["total_cost_usd"] >= 1.5
    assert profit["profit_usd"] == profit["total_revenue_usd"] - profit["total_cost_usd"]


def test_metrics_missing_video_returns_404(client):
    resp = client.post("/videos/999/metrics", json={"views": 100})
    assert resp.status_code == 404
