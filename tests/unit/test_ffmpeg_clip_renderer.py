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

import re

from content_factory.diarization.base import SpeakerTurn  # noqa: E402
from content_factory.transcription.base import TranscriptSegment, TranscriptWord  # noqa: E402
from content_factory.video_clipping.base import ClipRenderRequest  # noqa: E402
from content_factory.video_clipping.providers.ffmpeg_clip_renderer import (  # noqa: E402
    _FRAME_SIZE,
    FfmpegClipRenderer,
    _build_captions,
    _group_words_into_cues,
)


def _strip_ass_tags(text: str) -> str:
    """Test helper only: strips ASS override tags (e.g. `{\\k40}`) so
    assertions can check the underlying spoken words without being
    coupled to the exact karaoke-tag values."""
    return re.sub(r"\{[^}]*\}", "", text)


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


class TestBuildCaptions:
    """Pure-function tests for _build_captions's timing/precedence logic."""

    def test_prefers_word_level_timing_over_segments_when_both_given(self):
        ass = _build_captions(
            hook_text=None,
            segments=[TranscriptSegment(start_s=0.0, end_s=5.0, text="a whole long segment of text")],
            words=[
                TranscriptWord(start_s=0.0, end_s=0.4, word="a"),
                TranscriptWord(start_s=0.4, end_s=0.8, word="real"),
                TranscriptWord(start_s=0.8, end_s=1.2, word="word"),
            ],
            speaker_turns=[],
            start_s=0.0,
            end_s=5.0,
        )
        assert "a real word" in _strip_ass_tags(ass)
        assert "a whole long segment of text" not in ass
        # The whole point of switching to ASS: each word carries its own
        # \k karaoke timing tag rather than being a plain static line.
        assert ass.count(r"\k") == 3

    def test_falls_back_to_segments_when_no_words_available(self):
        ass = _build_captions(
            hook_text=None,
            segments=[TranscriptSegment(start_s=0.0, end_s=5.0, text="segment-level caption")],
            words=[],
            speaker_turns=[],
            start_s=0.0,
            end_s=5.0,
        )
        assert "segment-level caption" in ass
        # Fallback (no word timing) has nothing to key a karaoke sweep off,
        # so it stays a plain line - no \k tags at all.
        assert r"\k" not in ass

    def test_word_level_cues_never_start_before_the_hook_cue_ends(self):
        ass = _build_captions(
            hook_text="A punchy hook",
            segments=[],
            words=[
                TranscriptWord(start_s=0.0, end_s=0.5, word="during"),
                TranscriptWord(start_s=0.5, end_s=1.0, word="hook"),
                TranscriptWord(start_s=4.0, end_s=4.5, word="after"),
                TranscriptWord(start_s=4.5, end_s=5.0, word="hook"),
            ],
            speaker_turns=[],
            start_s=0.0,
            end_s=6.0,
        )
        stripped = _strip_ass_tags(ass)
        assert "during hook" not in stripped
        assert "after hook" in stripped
        assert "A punchy hook" in stripped

    def test_only_includes_words_overlapping_the_requested_clip_range(self):
        ass = _build_captions(
            hook_text=None,
            segments=[],
            words=[
                TranscriptWord(start_s=0.0, end_s=0.5, word="before"),
                TranscriptWord(start_s=10.0, end_s=10.5, word="inside"),
                TranscriptWord(start_s=30.0, end_s=30.5, word="after"),
            ],
            speaker_turns=[],
            start_s=9.0,
            end_s=15.0,
        )
        assert "inside" in ass
        assert "before" not in ass
        assert "after" not in ass

    def test_karaoke_word_durations_follow_each_words_own_timing(self):
        """The \\k duration for each word (in centiseconds) should reflect
        real word-to-word timing, not an even split - the last word's
        duration is its own end-start, and earlier words extend until the
        next word begins (absorbing any small gap between them)."""
        ass = _build_captions(
            hook_text=None,
            segments=[],
            words=[
                TranscriptWord(start_s=0.0, end_s=0.3, word="one"),
                TranscriptWord(start_s=0.5, end_s=0.9, word="two"),
            ],
            speaker_turns=[],
            start_s=0.0,
            end_s=5.0,
        )
        # "one" runs from 0.0 until "two" starts at 0.5 -> 50 centiseconds.
        assert r"{\k50}one" in ass
        # "two" is the last word in its group -> its own 0.4s duration.
        assert r"{\k40}two" in ass

    def test_uses_default_style_when_only_one_speaker_is_present(self):
        """A single-speaker recording (the overwhelmingly common case, and
        what every clip looked like before diarization existed) must
        render identically to having no diarization data at all - no
        per-speaker style lines used."""
        ass = _build_captions(
            hook_text=None,
            segments=[],
            words=[TranscriptWord(start_s=1.0, end_s=1.5, word="hi")],
            speaker_turns=[SpeakerTurn(start_s=0.0, end_s=5.0, speaker_label="SPEAKER_00")],
            start_s=0.0,
            end_s=5.0,
        )
        assert "Default,,0,0,0,," in ass
        assert "Speaker0,,0,0,0,," not in ass

    def test_assigns_distinct_styles_to_distinct_speakers(self):
        """Two real speakers overlapping the clip's own word groups should
        end up on two distinct named styles, in first-seen order - not
        all forced onto Default, and not sharing one style."""
        ass = _build_captions(
            hook_text=None,
            segments=[],
            words=[
                TranscriptWord(start_s=0.0, end_s=0.5, word="first"),
                TranscriptWord(start_s=0.5, end_s=1.0, word="speaker"),
                # A real pause forces this into its own cue, so it gets its
                # own dominant-speaker lookup rather than being absorbed
                # into the first group.
                TranscriptWord(start_s=3.0, end_s=3.5, word="second"),
                TranscriptWord(start_s=3.5, end_s=4.0, word="speaker"),
            ],
            speaker_turns=[
                SpeakerTurn(start_s=0.0, end_s=1.0, speaker_label="SPEAKER_00"),
                SpeakerTurn(start_s=3.0, end_s=4.0, speaker_label="SPEAKER_01"),
            ],
            start_s=0.0,
            end_s=5.0,
        )
        assert "Speaker0,,0,0,0,," in ass
        assert "Speaker1,,0,0,0,," in ass

    def test_hook_stays_on_default_style_even_with_multiple_speakers(self):
        ass = _build_captions(
            hook_text="A punchy hook",
            segments=[],
            words=[
                TranscriptWord(start_s=4.0, end_s=4.5, word="first"),
                TranscriptWord(start_s=8.0, end_s=8.5, word="second"),
            ],
            speaker_turns=[
                SpeakerTurn(start_s=4.0, end_s=4.5, speaker_label="SPEAKER_00"),
                SpeakerTurn(start_s=8.0, end_s=8.5, speaker_label="SPEAKER_01"),
            ],
            start_s=0.0,
            end_s=10.0,
        )
        hook_line = next(line for line in ass.splitlines() if "A punchy hook" in line)
        assert hook_line.startswith("Dialogue: 0,")
        assert ",Default,,0,0,0,," in hook_line


def test_render_with_multi_speaker_diarization_still_produces_a_real_file(tmp_path, sample_video):
    """End-to-end smoke test for the per-speaker caption styling path -
    real ffmpeg, a real .ass file with more than one Style block, real
    file out."""
    renderer = FfmpegClipRenderer(storage_dir=tmp_path / "clips")
    request = ClipRenderRequest(
        clip_id=5,
        source_path=str(sample_video),
        start_s=2.0,
        end_s=6.0,
        hook_text=None,
        transcript_words=[
            TranscriptWord(start_s=2.0, end_s=2.3, word="hello"),
            TranscriptWord(start_s=5.0, end_s=5.3, word="hi"),
        ],
        speaker_turns=[
            SpeakerTurn(start_s=2.0, end_s=2.3, speaker_label="SPEAKER_00"),
            SpeakerTurn(start_s=5.0, end_s=5.3, speaker_label="SPEAKER_01"),
        ],
    )

    result = renderer.render(request)

    from pathlib import Path

    assert Path(result.asset_url).exists()
    assert Path(result.asset_url).stat().st_size > 0
