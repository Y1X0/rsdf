import json

from content_factory.video_production.captions import CaptionCue
from content_factory.video_production.renderer.base import RenderRequest
from content_factory.video_production.renderer.providers.null_renderer import NullRenderer


def test_null_renderer_writes_manifest_and_returns_result(tmp_path):
    renderer = NullRenderer(storage_dir=tmp_path / "video")
    request = RenderRequest(
        video_id=42,
        template_id="default_template_v1",
        hook_text="hook",
        script_text="full script",
        voiceover_audio_path="/tmp/audio.wav",
        captions=[CaptionCue(text="hook", start_s=0.0, end_s=1.0), CaptionCue(text="full script", start_s=1.0, end_s=4.0)],
    )

    result = renderer.render(request)

    assert result.provider == "null"
    assert result.duration_s == 4.0
    manifest = json.loads((tmp_path / "video" / "video_42_manifest.json").read_text())
    assert manifest["video_id"] == 42
    assert manifest["hook_text"] == "hook"
    assert len(manifest["captions"]) == 2


def test_null_renderer_falls_back_to_default_duration_without_captions(tmp_path):
    renderer = NullRenderer(storage_dir=tmp_path / "video")
    request = RenderRequest(
        video_id=1, template_id="t", hook_text="h", script_text="s", voiceover_audio_path=None, captions=[]
    )
    result = renderer.render(request)
    assert result.duration_s == 15.0


def test_video_renderer_factory_defaults_to_null(monkeypatch, tmp_path):
    from content_factory.config import Settings
    from content_factory.video_production.renderer.factory import get_video_renderer
    from content_factory.video_production.renderer.providers.null_renderer import NullRenderer as NR

    settings = Settings(renderer_backend="null", media_storage_dir=str(tmp_path))
    renderer = get_video_renderer(settings)
    assert isinstance(renderer, NR)
