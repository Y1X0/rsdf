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

from content_factory.transcription.base import TranscriptSegment, TranscriptWord  # noqa: E402
from content_factory.video_clipping.base import ClipRenderRequest  # noqa: E402
from content_factory.video_clipping.providers.ffmpeg_clip_renderer import (  # noqa: E402
    _FRAME_SIZE,
    FfmpegClipRenderer,
    _build_srt,
    _group_words_into_cues,
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


def test_render_with_word_level_timing_still_produces_a_real_file(tmp_path, sample_video):
    """End-to-end smoke test for the word-level caption path specifically
    (not just segment-level, which the tests above already cover) - real
    ffmpeg, real subtitles filter, real file out."""
    renderer = FfmpegClipRenderer(storage_dir=tmp_path / "clips")
    request = ClipRenderRequest(
        clip_id=4,
        source_path=str(sample_video),
        start_s=2.0,
        end_s=6.0,
        hook_text="Real hook",
        transcript_segments=[TranscriptSegment(start_s=2.0, end_s=6.0, text="the quick brown fox jumps")],
        transcript_words=[
            TranscriptWord(start_s=2.0, end_s=2.3, word="the"),
            TranscriptWord(start_s=2.3, end_s=2.6, word="quick"),
            TranscriptWord(start_s=2.6, end_s=3.0, word="brown"),
            TranscriptWord(start_s=3.0, end_s=3.3, word="fox"),
            TranscriptWord(start_s=3.3, end_s=3.7, word="jumps"),
        ],
    )

    result = renderer.render(request)

    from pathlib import Path

    assert Path(result.asset_url).exists()
    assert Path(result.asset_url).stat().st_size > 0


class TestGroupWordsIntoCues:
    """Pure-function tests for the word-grouping logic itself - the exact
    timing/grouping behavior is easier to assert precisely here than by
    inspecting a rendered video's burned-in pixels."""

    def test_groups_up_to_max_words_per_cue(self):
        # 0.3s per word, back-to-back with no gap at all - clearly, not
        # just barely, under _WORD_PAUSE_BREAK_S, so this only exercises
        # the count-based split, not the pause-based one.
        words = [TranscriptWord(start_s=i * 0.3, end_s=(i + 1) * 0.3, word=f"w{i}") for i in range(8)]
        cues = _group_words_into_cues(words)
        # _MAX_WORDS_PER_CUE is 4, so cues split by count.
        assert [c[2] for c in cues] == ["w0 w1 w2 w3", "w4 w5 w6 w7"]

    def test_is_robust_to_floating_point_noise_right_at_the_pause_threshold(self):
        """Regression test for a real bug caught while writing this test
        suite: 2.0 - 1.4 == 0.6000000000000001 in IEEE-754 double
        precision, not exactly 0.6 - a gap that is genuinely, semantically
        "exactly at the threshold" (as real Whisper word timings, not just
        this test's own arithmetic, can easily produce) must not
        non-deterministically trigger a break depending on which side of
        that representation noise the subtraction happens to land on."""
        words = [
            TranscriptWord(start_s=0.0, end_s=0.4, word="w0"),
            TranscriptWord(start_s=1.0, end_s=1.4, word="w1"),
            TranscriptWord(start_s=2.0, end_s=2.4, word="w2"),
            TranscriptWord(start_s=3.0, end_s=3.4, word="w3"),
        ]
        # Each gap here is exactly _WORD_PAUSE_BREAK_S (0.6) - at or just a
        # hair past it, purely from float representation, never further.
        cues = _group_words_into_cues(words)
        assert [c[2] for c in cues] == ["w0 w1 w2 w3"]

    def test_breaks_a_cue_early_on_a_real_pause(self):
        words = [
            TranscriptWord(start_s=0.0, end_s=0.3, word="hello"),
            TranscriptWord(start_s=0.3, end_s=0.6, word="world"),
            # A 2s gap here - a real pause, not just "more words".
            TranscriptWord(start_s=2.6, end_s=2.9, word="new"),
            TranscriptWord(start_s=2.9, end_s=3.2, word="phrase"),
        ]
        cues = _group_words_into_cues(words)
        assert [c[2] for c in cues] == ["hello world", "new phrase"]
        assert cues[0] == (0.0, 0.6, "hello world")
        assert cues[1] == (2.6, 3.2, "new phrase")


class TestBuildSrt:
    """Pure-function tests for _build_srt's timing/precedence logic."""

    def test_prefers_word_level_timing_over_segments_when_both_given(self):
        srt = _build_srt(
            hook_text=None,
            segments=[TranscriptSegment(start_s=0.0, end_s=5.0, text="a whole long segment of text")],
            words=[
                TranscriptWord(start_s=0.0, end_s=0.4, word="a"),
                TranscriptWord(start_s=0.4, end_s=0.8, word="real"),
                TranscriptWord(start_s=0.8, end_s=1.2, word="word"),
            ],
            start_s=0.0,
            end_s=5.0,
        )
        assert "a real word" in srt
        assert "a whole long segment of text" not in srt

    def test_falls_back_to_segments_when_no_words_available(self):
        srt = _build_srt(
            hook_text=None,
            segments=[TranscriptSegment(start_s=0.0, end_s=5.0, text="segment-level caption")],
            words=[],
            start_s=0.0,
            end_s=5.0,
        )
        assert "segment-level caption" in srt

    def test_word_level_cues_never_start_before_the_hook_cue_ends(self):
        srt = _build_srt(
            hook_text="A punchy hook",
            segments=[],
            words=[
                TranscriptWord(start_s=0.0, end_s=0.5, word="during"),
                TranscriptWord(start_s=0.5, end_s=1.0, word="hook"),
                TranscriptWord(start_s=4.0, end_s=4.5, word="after"),
                TranscriptWord(start_s=4.5, end_s=5.0, word="hook"),
            ],
            start_s=0.0,
            end_s=6.0,
        )
        assert "during hook" not in srt
        assert "after hook" in srt
        assert "A punchy hook" in srt

    def test_only_includes_words_overlapping_the_requested_clip_range(self):
        srt = _build_srt(
            hook_text=None,
            segments=[],
            words=[
                TranscriptWord(start_s=0.0, end_s=0.5, word="before"),
                TranscriptWord(start_s=10.0, end_s=10.5, word="inside"),
                TranscriptWord(start_s=30.0, end_s=30.5, word="after"),
            ],
            start_s=9.0,
            end_s=15.0,
        )
        assert "inside" in srt
        assert "before" not in srt
        assert "after" not in srt
