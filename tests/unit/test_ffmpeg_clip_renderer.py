"""Real-provider test for FfmpegClipRenderer — unlike most provider tests
in this codebase (mocked HTTP), there's no network call to mock here: the
whole point of this class is running a real local ffmpeg binary against a
real video file. Skips cleanly if the 'rendering' extra isn't installed
(imageio-ffmpeg/Pillow), matching test_redis_rate_limiter.py's "skip
cleanly if the real dependency isn't available" convention — but note this
is exactly the dependency CI/the Docker image now install by default
(requirements-lock.txt), since real cutting is core to the clip factory,
not an optional nicety."""

import subprocess

import pytest

imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")

from content_factory.transcription.base import TranscriptSegment  # noqa: E402
from content_factory.video_clipping.base import ClipRenderRequest  # noqa: E402
from content_factory.video_clipping.providers.ffmpeg_clip_renderer import (  # noqa: E402
    _FRAME_SIZE,
    FfmpegClipRenderer,
)


def _ffprobe_dimensions(ffmpeg_bin, path) -> tuple[int, int]:
    result = subprocess.run([ffmpeg_bin, "-i", str(path)], capture_output=True, text=True)
    for line in result.stderr.splitlines():
        if "Video:" in line:
            for token in line.split(","):
                token = token.strip()
                if "x" in token and token.split("x")[0].strip().isdigit():
                    w, h = token.split()[0].split("x")
                    return int(w), int(h)
    raise AssertionError(f"could not find video dimensions in ffmpeg output:\n{result.stderr}")


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    """A real, tiny, generated video file — no external download, no
    network call, just ffmpeg's own test-pattern generator."""
    path = tmp_path_factory.mktemp("ffmpeg_clip_test") / "sample.mp4"
    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440",
            "-t", "10", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True, capture_output=True,
    )
    return path


def test_render_actually_cuts_the_requested_range(tmp_path, sample_video):
    renderer = FfmpegClipRenderer(storage_dir=tmp_path / "clips")
    request = ClipRenderRequest(
        clip_id=1,
        source_path=str(sample_video),
        start_s=2.0,
        end_s=6.0,
        hook_text="Real hook text",
        transcript_segments=[
            TranscriptSegment(start_s=0.0, end_s=4.0, text="First real caption."),
            TranscriptSegment(start_s=4.0, end_s=9.0, text="Second real caption."),
        ],
    )

    result = renderer.render(request)

    assert result.provider == "ffmpeg"
    assert result.duration_s == 4.0
    from pathlib import Path

    asset_path = Path(result.asset_url)
    assert asset_path.exists()
    assert asset_path.stat().st_size > 0
    assert result.thumbnail_url is not None
    assert Path(result.thumbnail_url).exists()

    # Regression guard for a real production incident: proves the
    # memory-reduction fix actually took effect (a crash under 1080x1920 +
    # default preset on a memory-constrained host), not just that *a* file
    # got produced.
    width, height = _ffprobe_dimensions(imageio_ffmpeg.get_ffmpeg_exe(), asset_path)
    assert (width, height) == _FRAME_SIZE


def test_render_without_hook_or_segments_still_produces_a_real_file(tmp_path, sample_video):
    renderer = FfmpegClipRenderer(storage_dir=tmp_path / "clips")
    request = ClipRenderRequest(
        clip_id=2, source_path=str(sample_video), start_s=0.0, end_s=3.0, hook_text=None, transcript_segments=[]
    )

    result = renderer.render(request)

    from pathlib import Path

    assert Path(result.asset_url).exists()
    assert result.duration_s == 3.0


def test_render_raises_on_invalid_range(tmp_path, sample_video):
    renderer = FfmpegClipRenderer(storage_dir=tmp_path / "clips")
    request = ClipRenderRequest(
        clip_id=3, source_path=str(sample_video), start_s=5.0, end_s=5.0, hook_text=None, transcript_segments=[]
    )

    with pytest.raises(ValueError):
        renderer.render(request)
