"""Regression tests for P1-5 (PHASE1_AUDIT.md F4 — qc_status was hardcoded
to "passed" unconditionally, with no real check behind it)."""

from content_factory.services import qc_service
from content_factory.video_production.captions import CaptionCue
from content_factory.video_production.renderer.base import RenderResult
from content_factory.video_production.tts.base import TTSResult


class _StubScript:
    def __init__(self, target_duration_s=None):
        self.target_duration_s = target_duration_s


def _tts_result(*, audio_path: str, duration_s: float) -> TTSResult:
    return TTSResult(
        audio_path=audio_path, duration_s=duration_s, provider="silent",
        model="silent-placeholder", model_version="v1", cost_usd=0.0, duration_ms=1,
    )


def _render_result(*, asset_url: str, duration_s: float) -> RenderResult:
    return RenderResult(asset_url=asset_url, duration_s=duration_s, provider="null")


def test_qc_passes_for_a_well_formed_render(tmp_path):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"\x00" * 100)
    manifest_path = tmp_path / "video.json"
    manifest_path.write_text("{}")

    result = qc_service.run_automated_qc(
        script=_StubScript(target_duration_s=10),
        tts_result=_tts_result(audio_path=str(audio_path), duration_s=10.0),
        render_result=_render_result(asset_url=str(manifest_path), duration_s=10.0),
        captions=[CaptionCue(text="hello", start_s=0.0, end_s=10.0)],
    )
    assert result.passed is True
    assert all(result.checks.values())


def test_qc_fails_when_audio_file_is_missing(tmp_path):
    manifest_path = tmp_path / "video.json"
    manifest_path.write_text("{}")

    result = qc_service.run_automated_qc(
        script=_StubScript(target_duration_s=10),
        tts_result=_tts_result(audio_path=str(tmp_path / "does_not_exist.wav"), duration_s=10.0),
        render_result=_render_result(asset_url=str(manifest_path), duration_s=10.0),
        captions=[CaptionCue(text="hello", start_s=0.0, end_s=10.0)],
    )
    assert result.passed is False
    assert result.checks["audio_present"] is False
    assert "audio" in result.notes.lower()


def test_qc_fails_when_no_captions_generated(tmp_path):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"\x00" * 100)
    manifest_path = tmp_path / "video.json"
    manifest_path.write_text("{}")

    result = qc_service.run_automated_qc(
        script=_StubScript(target_duration_s=10),
        tts_result=_tts_result(audio_path=str(audio_path), duration_s=10.0),
        render_result=_render_result(asset_url=str(manifest_path), duration_s=10.0),
        captions=[],
    )
    assert result.passed is False
    assert result.checks["captions_cover_audio"] is False


def test_qc_fails_when_duration_wildly_mismatched_from_target(tmp_path):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"\x00" * 100)
    manifest_path = tmp_path / "video.json"
    manifest_path.write_text("{}")

    result = qc_service.run_automated_qc(
        script=_StubScript(target_duration_s=10),
        tts_result=_tts_result(audio_path=str(audio_path), duration_s=1.0),
        render_result=_render_result(asset_url=str(manifest_path), duration_s=1.0),  # target was 10s
        captions=[CaptionCue(text="hi", start_s=0.0, end_s=1.0)],
    )
    assert result.passed is False
    assert result.checks["duration_within_tolerance"] is False


def test_qc_fails_when_render_asset_missing_from_disk(tmp_path):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"\x00" * 100)

    result = qc_service.run_automated_qc(
        script=_StubScript(target_duration_s=10),
        tts_result=_tts_result(audio_path=str(audio_path), duration_s=10.0),
        render_result=_render_result(asset_url=str(tmp_path / "missing_video.mp4"), duration_s=10.0),
        captions=[CaptionCue(text="hello", start_s=0.0, end_s=10.0)],
    )
    assert result.passed is False
    assert result.checks["render_asset_present"] is False


def test_qc_skips_disk_check_for_remote_url_assets(tmp_path):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"\x00" * 100)

    result = qc_service.run_automated_qc(
        script=_StubScript(target_duration_s=10),
        tts_result=_tts_result(audio_path=str(audio_path), duration_s=10.0),
        render_result=_render_result(asset_url="https://example.com/video.mp4", duration_s=10.0),
        captions=[CaptionCue(text="hello", start_s=0.0, end_s=10.0)],
    )
    assert result.checks["render_asset_present"] is True


def test_qc_skips_duration_check_when_script_has_no_target(tmp_path):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"\x00" * 100)
    manifest_path = tmp_path / "video.json"
    manifest_path.write_text("{}")

    result = qc_service.run_automated_qc(
        script=_StubScript(target_duration_s=None),
        tts_result=_tts_result(audio_path=str(audio_path), duration_s=500.0),
        render_result=_render_result(asset_url=str(manifest_path), duration_s=500.0),
        captions=[CaptionCue(text="hello", start_s=0.0, end_s=500.0)],
    )
    assert result.checks["duration_within_tolerance"] is True
