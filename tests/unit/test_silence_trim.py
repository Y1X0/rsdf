"""Real-provider test for silence_trim.py - runs a real local ffmpeg
binary against a real generated audio file, same convention as
test_scene_detection.py and test_ffmpeg_clip_renderer.py."""

import subprocess

import pytest

imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")

from content_factory.video_clipping.silence_trim import trim_leading_trailing_silence  # noqa: E402


@pytest.fixture(scope="module")
def video_with_leading_and_trailing_silence(tmp_path_factory):
    """1s of real silence, then 2s of a real tone, then 1s of real
    silence again - a genuine, measurable dead-air pattern, not a guess
    about what silencedetect would react to."""
    path = tmp_path_factory.mktemp("silence_trim_test") / "silence.mp4"
    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:d=2",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1",
            "-f", "lavfi", "-i", "color=c=blue:size=320x240:rate=10:duration=4",
            "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[a]",
            "-map", "[a]", "-map", "3:v", "-shortest", str(path),
        ],
        check=True, capture_output=True,
    )
    return path


def test_trims_both_leading_and_trailing_silence(video_with_leading_and_trailing_silence):
    trimmed_start, trimmed_end = trim_leading_trailing_silence(
        str(video_with_leading_and_trailing_silence), start_s=0.0, end_s=4.0
    )
    assert trimmed_start == pytest.approx(1.0, abs=0.1)
    assert trimmed_end == pytest.approx(3.0, abs=0.1)


def test_never_trims_past_max_trim_s(video_with_leading_and_trailing_silence):
    trimmed_start, trimmed_end = trim_leading_trailing_silence(
        str(video_with_leading_and_trailing_silence), start_s=0.0, end_s=4.0, max_trim_s=0.2
    )
    assert trimmed_start <= 0.2
    assert trimmed_end >= 3.8


def test_returns_input_unchanged_for_a_nonexistent_file(tmp_path):
    result = trim_leading_trailing_silence(str(tmp_path / "missing.mp4"), start_s=1.0, end_s=5.0)
    assert result == (1.0, 5.0)


def test_returns_input_unchanged_when_range_is_all_speech(video_with_leading_and_trailing_silence):
    """The tone-only middle section (1.0-3.0s) has no silence at either
    edge, so nothing meaningful should be trimmed - real encoded audio's
    silence boundary lands a fraction of a millisecond off the nominal
    1.0/3.0 (real encoder frame quantization, not a logic error), so this
    checks "negligible or no change" rather than bit-exact equality."""
    trimmed_start, trimmed_end = trim_leading_trailing_silence(
        str(video_with_leading_and_trailing_silence), start_s=1.0, end_s=3.0
    )
    assert trimmed_start == pytest.approx(1.0, abs=0.01)
    assert trimmed_end == pytest.approx(3.0, abs=0.01)


def test_trims_correctly_when_the_clips_own_start_lands_mid_silence(video_with_leading_and_trailing_silence):
    """Regression test for a real bug found running the full pipeline live
    (not just this unit test in isolation): a clip whose selected start_s
    (0.5) lands *inside* an already-ongoing leading silence (0.0-1.0),
    rather than exactly at the silence's own start, must still be
    detected and trimmed - an earlier implementation using `-ss` input
    seeking reported inconsistent timestamps for exactly this shape of
    query and silently failed to trim anything."""
    trimmed_start, trimmed_end = trim_leading_trailing_silence(
        str(video_with_leading_and_trailing_silence), start_s=0.5, end_s=4.0
    )
    assert trimmed_start == pytest.approx(1.0, abs=0.1)
