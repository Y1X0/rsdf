import json

from content_factory.transcription.base import TranscriptSegment
from content_factory.video_clipping.base import ClipRenderRequest
from content_factory.video_clipping.providers.null_clip_renderer import NullClipRenderer


def test_null_clip_renderer_writes_manifest_and_returns_result(tmp_path):
    renderer = NullClipRenderer(storage_dir=tmp_path / "clips")
    request = ClipRenderRequest(
        clip_id=7,
        source_path="/tmp/source.mp4",
        start_s=2.0,
        end_s=9.5,
        hook_text="hook",
        transcript_segments=[TranscriptSegment(start_s=2.0, end_s=9.5, text="hello")],
    )

    result = renderer.render(request)

    assert result.provider == "null"
    assert result.duration_s == 7.5
    manifest = json.loads((tmp_path / "clips" / "clip_7_manifest.json").read_text())
    assert manifest["clip_id"] == 7
    assert manifest["start_s"] == 2.0
    assert manifest["end_s"] == 9.5
    assert manifest["hook_text"] == "hook"


def test_clip_renderer_factory_defaults_to_null():
    from content_factory.config import Settings
    from content_factory.video_clipping.factory import get_clip_renderer

    settings = Settings(clip_renderer_backend="null")
    renderer = get_clip_renderer(settings)
    assert isinstance(renderer, NullClipRenderer)


def test_clip_renderer_factory_resolves_ffmpeg():
    import pytest

    pytest.importorskip("imageio_ffmpeg")
    from content_factory.config import Settings
    from content_factory.video_clipping.factory import get_clip_renderer
    from content_factory.video_clipping.providers.ffmpeg_clip_renderer import FfmpegClipRenderer

    settings = Settings(clip_renderer_backend="ffmpeg")
    renderer = get_clip_renderer(settings)
    assert isinstance(renderer, FfmpegClipRenderer)
