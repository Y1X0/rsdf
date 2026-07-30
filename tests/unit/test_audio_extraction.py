"""Real-provider test for transcription/audio_extraction.py — no network
call to mock, this runs the real bundled ffmpeg binary against a real
video file, matching test_ffmpeg_clip_renderer.py's own convention."""

import subprocess
from pathlib import Path

import pytest

imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")

from content_factory.transcription.audio_extraction import (  # noqa: E402
    cleanup_extracted_audio,
    extract_compact_audio,
)


def _ffprobe_output(path) -> str:
    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run([ffmpeg_bin, "-i", str(path)], capture_output=True, text=True)
    return result.stderr


@pytest.fixture(scope="module")
def sample_video_with_audio(tmp_path_factory):
    path = tmp_path_factory.mktemp("audio_extraction_test") / "sample.mp4"
    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "testsrc=size=640x480:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "5", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True, capture_output=True,
    )
    return path


def test_extract_compact_audio_produces_a_real_audio_only_file(sample_video_with_audio):
    """Regression test for a real production risk found via code audit:
    sending the raw long-form source video straight to a hosted
    transcription API works for a short test clip and risks failing
    outright for a genuinely long recording (easily several GB), while its
    audio track alone compresses to a few MB - this proves the extracted
    file genuinely has no video stream at all, is mono, and is far smaller
    than the source."""
    extracted_path = extract_compact_audio(str(sample_video_with_audio))
    try:
        assert extracted_path is not None
        assert Path(extracted_path).exists()
        assert Path(extracted_path).stat().st_size > 0

        streams = _ffprobe_output(extracted_path)
        assert "Video:" not in streams
        assert "Audio:" in streams
        assert "mono" in streams
        assert "16000 Hz" in streams

        assert Path(extracted_path).stat().st_size < Path(sample_video_with_audio).stat().st_size
    finally:
        cleanup_extracted_audio(extracted_path)


def test_extract_compact_audio_cleanup_removes_file_and_temp_dir(sample_video_with_audio):
    extracted_path = extract_compact_audio(str(sample_video_with_audio))
    assert extracted_path is not None
    parent_dir = Path(extracted_path).parent

    cleanup_extracted_audio(extracted_path)

    assert not Path(extracted_path).exists()
    assert not parent_dir.exists()


def test_extract_compact_audio_returns_none_for_a_nonexistent_source():
    """Best-effort by design, matching scene_detection.py/silence_trim.py's
    own contract: a source that can't be read must never raise, so the
    caller can safely fall back to the original file path."""
    result = extract_compact_audio("/nonexistent/path/does-not-exist.mp4")
    assert result is None
