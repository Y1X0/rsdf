"""Real-provider test for TemplatePillowRenderer — like
test_ffmpeg_clip_renderer.py, this runs a real local ffmpeg binary (and
real Pillow frame drawing), not a mock: the whole point of this class is
producing a genuinely playable video file. Skips cleanly if the
'rendering' extra isn't installed, matching that same file's convention.
"""

import subprocess

import pytest

imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")

from content_factory.video_production.captions import CaptionCue  # noqa: E402
from content_factory.video_production.renderer.base import RenderRequest  # noqa: E402
from content_factory.video_production.renderer.providers.template_pillow import (  # noqa: E402
    _FRAME_SIZE,
    TemplatePillowRenderer,
)


def _ffprobe_dimensions(path) -> tuple[int, int]:
    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run(
        [ffmpeg_bin, "-i", str(path)], capture_output=True, text=True
    )
    # ffmpeg (no ffprobe bundled) prints stream info to stderr regardless
    # of exit code when given no output - parse the "WxH" it always logs.
    for line in result.stderr.splitlines():
        if "Video:" in line:
            for token in line.split(","):
                token = token.strip()
                if "x" in token and token.split("x")[0].strip().isdigit():
                    dims = token.split()[0]
                    w, h = dims.split("x")
                    return int(w), int(h)
    raise AssertionError(f"could not find video dimensions in ffmpeg output:\n{result.stderr}")


def test_render_produces_a_real_playable_video_at_the_reduced_frame_size(tmp_path):
    renderer = TemplatePillowRenderer(storage_dir=tmp_path / "video")
    request = RenderRequest(
        video_id=1,
        template_id="default",
        hook_text="A real hook for the render test",
        script_text="A real full script body for the render test.",
        voiceover_audio_path=None,
        captions=[CaptionCue(text="A real caption", start_s=0.0, end_s=2.0)],
        target_duration_s=3.0,
    )

    result = renderer.render(request)

    assert result.provider == "template_pillow"
    assert result.duration_s == 3.0

    from pathlib import Path

    asset_path = Path(result.asset_url)
    assert asset_path.is_file()
    assert asset_path.stat().st_size > 0

    # Proves the memory-reduction fix actually took effect, not just that
    # *a* file got produced - regression guard for the real production
    # incident (a crash under 1080x1920 + default preset on a
    # memory-constrained host).
    width, height = _ffprobe_dimensions(asset_path)
    assert (width, height) == _FRAME_SIZE
