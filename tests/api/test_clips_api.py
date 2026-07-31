import io


def _upload_source_video(client, *, title="My Long Video"):
    fake_video_bytes = b"not a real video, just test bytes"
    return client.post(
        "/source-videos",
        data={"title": title},
        files={"file": ("test.mp4", io.BytesIO(fake_video_bytes), "video/mp4")},
    )


def test_upload_source_video_creates_row_and_stores_file(client):
    resp = _upload_source_video(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "My Long Video"
    assert body["transcription_status"] == "pending"
    assert body["analysis_status"] == "pending"


def test_upload_source_video_still_defaults_to_upload_source(client):
    """Regression test for the Content Rewards Connector's Milestone 1 DB
    change (source_videos.source/external_source_id): the existing manual
    upload path must be completely unaffected."""
    resp = _upload_source_video(client)
    body = resp.json()
    assert body["source"] == "upload"
    assert body["external_source_id"] is None


def test_list_and_get_source_video(client):
    created = _upload_source_video(client).json()

    list_resp = client.get("/source-videos")
    assert list_resp.status_code == 200
    assert any(v["id"] == created["id"] for v in list_resp.json())

    get_resp = client.get(f"/source-videos/{created['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == created["id"]


def test_get_source_video_404_when_missing(client):
    resp = client.get("/source-videos/999999")
    assert resp.status_code == 404


def test_transcribe_with_null_default_provider_completes_with_empty_transcript(client):
    """No TRANSCRIPTION_PROVIDER configured in the test environment - the
    safe null default must complete cleanly rather than crash or hang."""
    created = _upload_source_video(client).json()
    resp = client.post(f"/source-videos/{created['id']}/transcribe", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["transcription_status"] == "completed"
    assert body["transcript_text"] == ""


def test_analyze_with_no_transcript_segments_yields_no_clips(client):
    """The fake LLM's default response builder returns "[]" for a JSON-array
    prompt, and an empty transcript means the agent has nothing to work
    with either way - both must degrade to zero clips, not an error."""
    created = _upload_source_video(client).json()
    client.post(f"/source-videos/{created['id']}/transcribe", json={})

    resp = client.post(f"/source-videos/{created['id']}/analyze", json={"max_clips": 5})
    assert resp.status_code == 200
    assert resp.json() == []


def test_analyze_records_hook_usage_into_the_shared_hook_library(client):
    """Regression test for a real gap: clip-factory hooks previously never
    reached content_intelligence.record_hook_usage at all (only Script-
    pipeline hooks did, via api/routers/content.py's own equivalent call) -
    so get_top_hooks' retrieval never learned anything from clip hooks."""
    import json

    from content_factory.api import deps
    from content_factory.api.main import app
    from content_factory.llm.providers.fake_provider import FakeLLMClient

    created = _upload_source_video(client).json()
    client.post(f"/source-videos/{created['id']}/transcribe", json={})

    canned = [
        {
            "start_s": 0.0, "end_s": 5.0,
            "hook_framework": "curiosity_gap",
            "hook_text": "a very specific clip hook for this test",
            "predicted_score": 0.8, "reason": "n/a",
        }
    ]
    clip_llm = FakeLLMClient(response_builder=lambda system, prompt: json.dumps(canned))
    app.dependency_overrides[deps.get_llm_client] = lambda: clip_llm
    try:
        resp = client.post(f"/source-videos/{created['id']}/analyze", json={"max_clips": 5})
    finally:
        del app.dependency_overrides[deps.get_llm_client]

    assert resp.status_code == 200
    clips = resp.json()
    assert len(clips) == 1
    assert clips[0]["hook_text"] == "a very specific clip hook for this test"

    from content_factory.services import content_intelligence

    db = client.db_session_factory()
    try:
        hooks = content_intelligence.get_top_hooks(db, niche_id=None)
        matching = [h for h in hooks if h.hook_text == "a very specific clip hook for this test"]
        assert len(matching) == 1
        assert matching[0].times_used == 1
    finally:
        db.close()


def test_list_clips_for_source_video(client):
    created = _upload_source_video(client).json()
    resp = client.get(f"/source-videos/{created['id']}/clips")
    assert resp.status_code == 200
    assert resp.json() == []


def test_render_clip_404_when_missing(client):
    resp = client.post("/clips/999999/render", json={})
    assert resp.status_code == 404


def test_render_clip_with_null_default_renderer_produces_video_ready_for_review(client):
    """Full path through the real API: upload -> (manually seed a
    transcript + a suggested clip, since the fake LLM's default builder
    won't produce one) -> render -> the resulting Video flows straight into
    the *existing*, unmodified review endpoint."""
    created = _upload_source_video(client).json()
    client.post(f"/source-videos/{created['id']}/transcribe", json={})

    # Seed a clip directly the same way analyze() would have, since driving
    # the fake LLM to emit a real clip candidate through the HTTP layer
    # isn't the point of this test - the render/review path is.
    from content_factory.db.models.clip import Clip

    db = client.db_session_factory()
    try:
        clip = Clip(source_video_id=created["id"], start_s=0.0, end_s=5.0, hook_text="hook")
        db.add(clip)
        db.commit()
        clip_id = clip.id
    finally:
        db.close()

    render_resp = client.post(f"/clips/{clip_id}/render", json={})
    assert render_resp.status_code == 200
    video = render_resp.json()
    assert video["clip_id"] == clip_id
    assert video["script_id"] is None
    assert video["status"] == "pending_review"
    assert video["render_status"] == "completed"
    assert video["contains_ai_voice"] is False
    assert video["contains_ai_visual"] is False

    review_resp = client.post(f"/videos/{video['id']}/review", json={"decision": "approved"})
    assert review_resp.status_code == 200
    assert review_resp.json()["decision"] == "approved"


def test_render_clip_is_idempotent(client):
    created = _upload_source_video(client).json()

    from content_factory.db.models.clip import Clip

    db = client.db_session_factory()
    try:
        clip = Clip(source_video_id=created["id"], start_s=0.0, end_s=5.0)
        db.add(clip)
        db.commit()
        clip_id = clip.id
    finally:
        db.close()

    first = client.post(f"/clips/{clip_id}/render", json={"idempotency_key": "clip-render-key"})
    second = client.post(f"/clips/{clip_id}/render", json={"idempotency_key": "clip-render-key"})
    assert first.json()["id"] == second.json()["id"]


def test_render_clip_rejects_a_second_render_without_matching_idempotency_key(client):
    """Real gap idempotency keys alone don't cover: a *different* request
    (a new/different idempotency_key, e.g. a client that doesn't reuse the
    same key across a retry) against a clip that's already rendered used
    to silently create a second Video row colliding on the same output
    file path (clip_{id}.mp4) as the first - see
    clip_service.ClipAlreadyRendered. Two calls with no key at all don't
    reproduce this (the fingerprint computed from the identical payload
    itself becomes the idempotency key, so the second is correctly
    replayed) - it's specifically a *different* key pointing at the same
    already-rendered clip that idempotency has no way to catch."""
    created = _upload_source_video(client).json()

    from content_factory.db.models.clip import Clip

    db = client.db_session_factory()
    try:
        clip = Clip(source_video_id=created["id"], start_s=0.0, end_s=5.0)
        db.add(clip)
        db.commit()
        clip_id = clip.id
    finally:
        db.close()

    first = client.post(f"/clips/{clip_id}/render", json={"idempotency_key": "attempt-1"})
    assert first.status_code == 200

    second = client.post(f"/clips/{clip_id}/render", json={"idempotency_key": "attempt-2"})
    assert second.status_code == 409
    assert "already been rendered" in second.json()["detail"]


def test_source_video_endpoints_require_authentication(unauthenticated_client):
    resp = unauthenticated_client.get("/source-videos")
    assert resp.status_code == 401


class _FakeContentSourceProvider:
    """Test double standing in for the real content_sources provider — two
    videos so the sync endpoint's per-video idempotency/loop logic is
    actually exercised, not just a single-item happy path."""

    def __init__(self):
        self.download_calls = []

    def list_available_videos(self):
        from content_factory.content_sources.base import RemoteCampaignVideo

        return [
            RemoteCampaignVideo(
                external_id="ext-1", title="Fake Video 1", campaign_name="fake-campaign",
                duration_s=5.0, download_url="", source_page_url="https://example.test/1",
            ),
            RemoteCampaignVideo(
                external_id="ext-2", title="Fake Video 2", campaign_name="fake-campaign",
                duration_s=5.0, download_url="", source_page_url="https://example.test/2",
            ),
        ]

    def download_video(self, video, destination_path):
        self.download_calls.append(video.external_id)
        with open(destination_path, "wb") as f:
            f.write(b"fake downloaded bytes")


def test_sync_content_rewards_registers_videos_from_fake_provider(client):
    from content_factory.api import deps
    from content_factory.api.main import app

    fake_provider = _FakeContentSourceProvider()
    app.dependency_overrides[deps.get_content_source_provider] = lambda: fake_provider
    try:
        resp = client.post("/source-videos/sync-content-rewards")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 2
        assert {r["external_id"] for r in body["results"]} == {"ext-1", "ext-2"}
        assert all(r["created"] for r in body["results"])
        assert sorted(fake_provider.download_calls) == ["ext-1", "ext-2"]

        first_video_id = body["results"][0]["source_video_id"]
        get_resp = client.get(f"/source-videos/{first_video_id}")
        assert get_resp.json()["source"] == "content_rewards"
        assert get_resp.json()["external_source_id"] == "ext-1"
    finally:
        del app.dependency_overrides[deps.get_content_source_provider]


def test_sync_content_rewards_is_idempotent_on_rerun(client):
    from content_factory.api import deps
    from content_factory.api.main import app

    fake_provider = _FakeContentSourceProvider()
    app.dependency_overrides[deps.get_content_source_provider] = lambda: fake_provider
    try:
        first = client.post("/source-videos/sync-content-rewards").json()
        second = client.post("/source-videos/sync-content-rewards").json()

        assert {r["source_video_id"] for r in first["results"]} == {
            r["source_video_id"] for r in second["results"]
        }
        assert all(not r["created"] for r in second["results"])
        # Only downloaded once each - the second sync must not re-fetch.
        assert sorted(fake_provider.download_calls) == ["ext-1", "ext-2"]
    finally:
        del app.dependency_overrides[deps.get_content_source_provider]


def test_sync_content_rewards_requires_authentication(unauthenticated_client):
    resp = unauthenticated_client.post("/source-videos/sync-content-rewards")
    assert resp.status_code == 401
