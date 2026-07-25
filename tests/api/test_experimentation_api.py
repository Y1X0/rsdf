"""Phase 2 M6 — Experimentation Engine, exercised through the real HTTP
layer: run computes recommendations only, apply is the separate explicit
human action that actually changes anything."""


def _create_video_with_score(client, *, niche_name: str, hook_text_suffix: str) -> dict:
    campaign = client.post(
        "/campaigns", json={"brand_name": "Acme", "niche_name": niche_name, "cpm_rate": 3.0}
    ).json()
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": f"idea {hook_text_suffix}"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    video = client.post(f"/scripts/{scripts[0]['id']}/render", json={}).json()
    client.post(
        f"/videos/{video['id']}/metrics",
        json={"views": 10000, "avg_watch_time_s": 30, "completion_rate": 0.9, "shares": 60, "comments": 120, "likes": 1200},
    )
    return {**video, "niche_id": campaign["niche_id"]}


def test_run_experiment_returns_results_without_mutating_anything(client):
    niche_id = None
    for i in range(3):
        video = _create_video_with_score(client, niche_name="finance", hook_text_suffix=str(i))
        niche_id = video["niche_id"]

    resp = client.post("/experimentation/run", json={"axis": "hook", "niche_id": niche_id, "min_sample_size": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["experiment"]["axis"] == "hook"
    assert body["experiment"]["status"] == "inconclusive"  # below min_sample_size
    assert all(r["is_winner"] is False for r in body["results"])
    assert all(r["applied_at"] is None for r in body["results"])


def test_non_operator_cannot_run_experiment(client):
    from content_factory.auth.jwt_service import create_access_token
    from content_factory.config import get_settings

    viewer_token = create_access_token(subject="viewer", role="viewer", settings=get_settings())
    resp = client.post(
        "/experimentation/run",
        json={"axis": "hook"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


def test_list_recommendations_defaults_to_winners_only(client):
    resp = client.get("/experimentation/recommendations")
    assert resp.status_code == 200
    assert resp.json() == []  # nothing computed yet


def test_apply_recommendation_requires_a_winner(client):
    video = _create_video_with_score(client, niche_name="finance", hook_text_suffix="only-one")
    resp = client.post(
        "/experimentation/run", json={"axis": "hook", "niche_id": video["niche_id"], "min_sample_size": 1}
    )
    result_id = resp.json()["results"][0]["id"]
    assert resp.json()["results"][0]["is_winner"] is False  # a field of one can't beat a baseline

    apply_resp = client.post(
        f"/experimentation/recommendations/{result_id}/apply", json={"applied_by": "operator1"}
    )
    assert apply_resp.status_code == 409


def test_get_experiment_by_id(client):
    resp = client.post("/experimentation/run", json={"axis": "niche"})
    experiment_id = resp.json()["experiment"]["id"]

    get_resp = client.get(f"/experimentation/{experiment_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == experiment_id
