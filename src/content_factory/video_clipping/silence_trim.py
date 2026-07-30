"""Leading/trailing silence trimming: a selected clip range's exact edges
often land a fraction of a second before the speaker actually starts, or
a moment after they've already stopped (a natural artifact of
timestamp-based selection, whether the timestamp came from a transcript
or a detected scene cut) - this uses ffmpeg's own `silencedetect` audio
filter on the *whole* source file (never with `-ss` seeking) to get a
list of real silence intervals in unambiguous absolute timestamps, then
intersects that list with the requested [start_s, end_s] range in plain
Python to decide how much to trim off each edge before the clip is cut.

Whole-file, not `-ss`-seeked: an earlier version of this function used
`-ss <start_s> -i <file>` to only decode the clip's own small slice,
which seemed cheaper - and was seen to work in one early verification
video. Running the real, pushed feature end-to-end against a second,
differently-authored test video (during a live demo requested to
actually exercise the deployed code, not just a unit test) surfaced a
real bug: `-ss` before `-i` does not consistently yield absolute,
original-file timestamps in `silencedetect`'s output across different
source containers - in one file the reported timestamps stayed
absolute, in another they were effectively reset to the seek point.
Confirmed directly, both ways, with real ffmpeg runs, rather than
assumed from either result alone (see the first verification history in
this project's own commit log for the original, incomplete test). A
whole-file scan with no seek at all has no such ambiguity - a
long-documented, reliable ffmpeg behavior - at the cost of decoding the
full source once per render call rather than just the requested slice.
That's still a decode-only pass (no libx264 encode buffers), the same
cost class this app's scene_detection.py already accepts without
per-call caching, not a reintroduction of the measured encode-time OOM
this project fixed elsewhere.

Best-effort by design, same posture as scene_detection.py: any failure
(ffmpeg missing, file missing, detection error) returns the original
`start_s`/`end_s` unchanged rather than raising.
"""

import re
import subprocess
from pathlib import Path

from content_factory.logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_NOISE_THRESHOLD_DB = -30.0
_DEFAULT_MIN_SILENCE_DURATION_S = 0.3
_DEFAULT_MAX_TRIM_S = 2.0
_BOUNDARY_MATCH_TOLERANCE_S = 0.1
_MIN_RESULTING_DURATION_S = 1.0
_DETECTION_TIMEOUT_S = 60

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[0-9.]+)")


def _detect_absolute_silence_intervals(
    source_path: str, *, noise_threshold_db: float, min_silence_duration_s: float
) -> list[tuple[float, float]]:
    try:
        import imageio_ffmpeg
    except ImportError:
        return []

    try:
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run(
            [
                ffmpeg_bin,
                "-i", source_path,
                "-af", f"silencedetect=noise={noise_threshold_db}dB:d={min_silence_duration_s}",
                "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            timeout=_DETECTION_TIMEOUT_S,
        )
    except Exception:
        logger.warning("silence_detection_failed", source_path=source_path, exc_info=True)
        return []

    starts = [float(m) for m in _SILENCE_START_RE.findall(result.stderr)]
    ends = [float(m) for m in _SILENCE_END_RE.findall(result.stderr)]
    return list(zip(starts, ends))


def trim_leading_trailing_silence(
    source_path: str,
    *,
    start_s: float,
    end_s: float,
    noise_threshold_db: float = _DEFAULT_NOISE_THRESHOLD_DB,
    min_silence_duration_s: float = _DEFAULT_MIN_SILENCE_DURATION_S,
    max_trim_s: float = _DEFAULT_MAX_TRIM_S,
) -> tuple[float, float]:
    if end_s - start_s <= 0 or not Path(source_path).exists():
        return start_s, end_s

    intervals = _detect_absolute_silence_intervals(
        source_path, noise_threshold_db=noise_threshold_db, min_silence_duration_s=min_silence_duration_s
    )

    trimmed_start, trimmed_end = start_s, end_s

    # Leading: a silence interval that starts at/before the clip's own
    # start and reaches into it (covers the case where start_s lands
    # exactly on a silence's own start, and the case where start_s lands
    # somewhere in the middle of an already-ongoing silence).
    for interval_start, interval_end in intervals:
        if interval_start <= start_s + _BOUNDARY_MATCH_TOLERANCE_S and interval_end > start_s:
            trimmed_start = min(start_s + max_trim_s, interval_end)
            break

    # Trailing: a silence interval that ends at/after the clip's own end
    # and reaches back into it.
    for interval_start, interval_end in intervals:
        if interval_end >= end_s - _BOUNDARY_MATCH_TOLERANCE_S and interval_start < end_s:
            trimmed_end = max(end_s - max_trim_s, interval_start)
            break

    if trimmed_end - trimmed_start < _MIN_RESULTING_DURATION_S:
        return start_s, end_s

    if (trimmed_start, trimmed_end) != (start_s, end_s):
        logger.info(
            "silence_trimmed",
            source_path=source_path,
            original=(start_s, end_s),
            trimmed=(trimmed_start, trimmed_end),
        )
    return trimmed_start, trimmed_end
