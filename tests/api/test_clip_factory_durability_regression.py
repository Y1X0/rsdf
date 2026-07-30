"""Regression tests for a real bug found by actually running the clip
factory pipeline end-to-end through the real HTTP API with a real (Groq)
transcription provider, not just this suite's own unit tests: a real
provider failure left `source_video.transcription_status` durably stuck
at IN_PROGRESS instead of FAILED, because clip_service.py's own except
blocks only `db.flush()`ed the terminal status write - api/deps.get_db()
then rolled back the *whole* session when the exception reached it,
silently wiping that flush along with everything else. This is exactly
the P0-1/P0-2 commit-boundary lesson agent_run() itself already applies
(see agents/base.py and tests/api/test_durability_regression.py) - just
never carried over to this newer pipeline.

These tests go through the real HTTP layer with a deliberately failing
provider, matching test_durability_regression.py's own convention: the
gap this reproduces is specifically that no earlier test exercised the
real request/response rollback cycle, only the service function in
isolation against a bare, never-rolled-back session.
"""

import io

from content_factory.db.models.enums import ProcessingStatus
from content_factory.db.models.source_video import SourceVideo


def _upload_source_video(client, *, title="Durability Test Video"):
    fake_video_bytes = b"not a real video, just test bytes"
    return client.post(
        "/source-videos",
        data={"title": title},
        files={"file": ("test.mp4", io.BytesIO(fake_video_bytes), "video/mp4")},
    )


class _BoomTranscriptionProvider:
    def transcribe(self, audio_path: str):
        raise RuntimeError("simulated real transcription provider failure")


class _BoomLLMClient:
    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024):
        raise RuntimeError("simulated real LLM provider failure")


def test_transcription_failure_status_survives_the_real_request_rollback(client):
    from content_factory.api import deps
    from content_factory.api.main import app

    created = _upload_source_video(client).json()

    app.dependency_overrides[deps.get_transcription_provider] = lambda: _BoomTranscriptionProvider()
    try:
        resp = client.post(f"/source-videos/{created['id']}/transcribe", json={})
    finally:
        del app.dependency_overrides[deps.get_transcription_provider]

    assert resp.status_code == 500

    db = client.db_session_factory()
    try:
        source_video = db.get(SourceVideo, created["id"])
        assert source_video.transcription_status == ProcessingStatus.FAILED
    finally:
        db.close()


def test_analysis_failure_status_survives_the_real_request_rollback(client):
    from content_factory.api import deps
    from content_factory.api.main import app

    created = _upload_source_video(client).json()
    client.post(f"/source-videos/{created['id']}/transcribe", json={})

    app.dependency_overrides[deps.get_llm_client] = lambda: _BoomLLMClient()
    try:
        resp = client.post(f"/source-videos/{created['id']}/analyze", json={"max_clips": 5})
    finally:
        del app.dependency_overrides[deps.get_llm_client]

    assert resp.status_code == 500

    db = client.db_session_factory()
    try:
        source_video = db.get(SourceVideo, created["id"])
        assert source_video.analysis_status == ProcessingStatus.FAILED
    finally:
        db.close()


def test_transcription_can_be_retried_after_a_failure_through_the_real_api(client):
    """Not just "the status looks right once" - a subsequent real retry
    must actually be able to proceed (this only holds if the earlier
    IN_PROGRESS -> FAILED transition genuinely committed; if it hadn't,
    the row would still show IN_PROGRESS, which nothing currently blocks
    on, but a stuck status is still the wrong thing for an operator to see)."""
    from content_factory.api import deps
    from content_factory.api.main import app

    created = _upload_source_video(client).json()

    app.dependency_overrides[deps.get_transcription_provider] = lambda: _BoomTranscriptionProvider()
    first = client.post(f"/source-videos/{created['id']}/transcribe", json={})
    assert first.status_code == 500
    del app.dependency_overrides[deps.get_transcription_provider]

    second = client.post(f"/source-videos/{created['id']}/transcribe", json={})
    assert second.status_code == 200
    assert second.json()["transcription_status"] == "completed"
