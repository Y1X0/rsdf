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


def test_source_video_endpoints_require_authentication(unauthenticated_client):
    resp = unauthenticated_client.get("/source-videos")
    assert resp.status_code == 401
