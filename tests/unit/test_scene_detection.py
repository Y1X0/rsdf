"""Real-provider test for scene_detection.py - like
test_ffmpeg_clip_renderer.py, there's no network call to mock: the whole
point is running a real local ffmpeg binary against a real video file.
Skips cleanly if the 'rendering' extra isn't installed, matching that
file's convention."""

import subprocess

import pytest

imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")

from content_factory.video_clipping.scene_detection import (  # noqa: E402
    detect_scene_changes,
    snap_to_nearest_scene_change,
)


@pytest.fixture(scope="module")
def video_with_a_real_hard_cut(tmp_path_factory):
    """Two visually distinct patterns concatenated - a genuine hard cut at
    4.0s, not a guess about what ffmpeg's scene-difference score would
    react to."""
    path = tmp_path_factory.mktemp("scene_detection_test") / "cut.mp4"
    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=4",
            "-f", "lavfi", "-i", "smptebars=size=320x240:rate=10:duration=4",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True, capture_output=True,
    )
    return path


def test_detects_a_real_scene_cut(video_with_a_real_hard_cut):
    changes = detect_scene_changes(str(video_with_a_real_hard_cut))
    assert any(abs(t - 4.0) < 0.5 for t in changes)


def test_returns_empty_list_for_a_nonexistent_file(tmp_path):
    assert detect_scene_changes(str(tmp_path / "does-not-exist.mp4")) == []


def test_returns_empty_list_for_a_file_with_no_real_cuts(tmp_path):
    """A single unchanging pattern should produce no scene-change
    timestamps at all - not a false positive on every frame."""
    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    path = tmp_path / "no_cuts.mp4"
    subprocess.run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=blue:size=320x240:rate=10:duration=3",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True, capture_output=True,
    )
    assert detect_scene_changes(str(path)) == []


class TestSnapToNearestSceneChange:
    def test_snaps_when_within_tolerance(self):
        assert snap_to_nearest_scene_change(9.0, [10.0], tolerance_s=2.0) == 10.0

    def test_does_not_snap_when_outside_tolerance(self):
        assert snap_to_nearest_scene_change(9.0, [20.0], tolerance_s=2.0) == 9.0

    def test_returns_input_unchanged_when_no_scene_changes_given(self):
        assert snap_to_nearest_scene_change(9.0, []) == 9.0

    def test_picks_the_closest_of_several_candidates(self):
        assert snap_to_nearest_scene_change(9.0, [1.0, 8.5, 30.0], tolerance_s=2.0) == 8.5
