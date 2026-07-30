"""Regression tests for P0-1 and P0-2 (PHASE1_AUDIT.md F1): a request that
fails partway through used to roll back *everything* flushed earlier in
that same request, including an already-completed, already-"billed" step's
AgentRun and CostLedger rows — and the idempotency service's FAILED-record
retry path was reachable only in isolated unit tests, not through the real
API, because the FAILED record itself never survived the same rollback.

These tests exercise the fix through the real HTTP layer, with a
deliberately flaky TTS/renderer pair, rather than only at the service layer
— the previous test suite's gap (per the v1 audit) was exactly that no test
went through the actual request/response cycle to check this.
"""

from dataclasses import replace

from content_factory.db.models.agent_run import AgentRun
from content_factory.db.models.analytics import CostLedger
from content_factory.db.models.enums import ProcessingStatus, VideoStatus
from content_factory.db.models.video import Video
from content_factory.video_production.renderer.providers.null_renderer import NullRenderer
from content_factory.video_production.tts.providers.silent_provider import SilentTTSProvider


class _PaidSilentTTS:
    """Like SilentTTSProvider, but with a nonzero cost, so the test can
    prove a CostLedger entry is durable too — the real SilentTTSProvider
    always reports cost_usd=0.0, which record_agent_run_cost intentionally
    skips ledgering (nothing to record)."""

    def __init__(self, storage_dir):
        self._delegate = SilentTTSProvider(storage_dir=storage_dir)

    def synthesize(self, *, text: str, voice_id: str):
        result = self._delegate.synthesize(text=text, voice_id=voice_id)
        return replace(result, cost_usd=0.05, provider="paid_silent_test_double")


class _FlakyRenderer:
    """Fails on its first call, succeeds (delegating to the real
    NullRenderer) on every call after that — simulates a transient
    provider failure and a subsequent successful retry."""

    def __init__(self, storage_dir):
        self._storage_dir = storage_dir
        self.call_count = 0

    def render(self, request):
        self.call_count += 1
        if self.call_count == 1:
            raise RuntimeError("simulated transient renderer failure")
        return NullRenderer(storage_dir=self._storage_dir).render(request)


def _create_script(client) -> dict:
    campaign = client.post("/campaigns", json={"brand_name": "Acme", "cpm_rate": 3.0}).json()
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    return scripts[0]


def test_completed_step_survives_a_later_step_failing_in_the_same_request(client, tmp_path):
    """F1's core regression: TTS succeeds (and is billed) -> render fails
    -> the whole HTTP request errors -> but the TTS AgentRun/CostLedger and
    the Video row itself must still be durably present afterward."""
    from content_factory.api import deps
    from content_factory.api.main import app

    script = _create_script(client)

    flaky_renderer = _FlakyRenderer(storage_dir=tmp_path / "video")
    paid_tts = _PaidSilentTTS(storage_dir=tmp_path / "audio")
    app.dependency_overrides[deps.get_video_renderer] = lambda: flaky_renderer
    app.dependency_overrides[deps.get_tts_provider] = lambda: paid_tts

    resp = client.post(f"/scripts/{script['id']}/render", json={"idempotency_key": "durability-test-key"})
    assert resp.status_code == 500
    assert flaky_renderer.call_count == 1

    db = client.db_session_factory()
    try:
        tts_run = db.query(AgentRun).filter(AgentRun.agent_name == "tts_provider").one()
        assert tts_run.status == ProcessingStatus.COMPLETED
        assert float(tts_run.cost_usd) == 0.05

        render_run = db.query(AgentRun).filter(AgentRun.agent_name == "video_renderer").one()
        assert render_run.status == ProcessingStatus.FAILED
        assert "simulated transient renderer failure" in render_run.error_message

        cost_entries = db.query(CostLedger).filter(CostLedger.agent_run_id == tts_run.id).all()
        assert len(cost_entries) == 1
        assert float(cost_entries[0].cost_usd) == 0.05

        video = db.query(Video).filter(Video.script_id == script["id"]).one()
        assert video.render_status == ProcessingStatus.FAILED
        assert video.status == VideoStatus.RENDER_FAILED
        assert video.tts_agent_run_id == tts_run.id
    finally:
        db.close()


def test_retrying_after_a_failure_succeeds_through_the_real_api(client, tmp_path):
    """P0-2's core regression: the same idempotency key, retried after a
    failure, must actually redo (and this time complete) the work — not
    404, not 409, not silently return nothing."""
    from content_factory.api import deps
    from content_factory.api.main import app

    script = _create_script(client)

    flaky_renderer = _FlakyRenderer(storage_dir=tmp_path / "video")
    app.dependency_overrides[deps.get_video_renderer] = lambda: flaky_renderer

    first = client.post(f"/scripts/{script['id']}/render", json={"idempotency_key": "retry-test-key"})
    assert first.status_code == 500

    second = client.post(f"/scripts/{script['id']}/render", json={"idempotency_key": "retry-test-key"})
    assert second.status_code == 200
    body = second.json()
    assert body["render_status"] == "completed"
    assert body["status"] == "pending_review"
    assert flaky_renderer.call_count == 2

    # And a third call with the same key now replays the completed result
    # rather than re-running the renderer a third time.
    third = client.post(f"/scripts/{script['id']}/render", json={"idempotency_key": "retry-test-key"})
    assert third.status_code == 200
    assert third.json()["id"] == body["id"]
    assert flaky_renderer.call_count == 2
