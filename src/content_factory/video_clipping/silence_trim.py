"""Leading/trailing silence trimming: a selected clip range's exact edges
often land a fraction of a second before the speaker actually starts, or
a moment after they've already stopped (a natural artifact of
timestamp-based selection, whether the timestamp came from a transcript
or a detected scene cut) - this uses ffmpeg's own `silencedetect` audio
filter on just the requested [start_s, end_s] slice (a small, cheap
decode of one clip's own range, not a whole-file pass) to trim that dead
air from both ends before the clip is actually cut.

Verified directly (not assumed) before writing this as "working code":
built a real 4s test file (1s silence, 2s tone, 1s silence) and confirmed
two things that aren't obvious from ffmpeg's own docs alone - (1) with
`-ss <n> -i <file>` (input seeking, the same form the renderer already
uses), `silencedetect`'s reported `silence_start`/`silence_end` are
absolute timestamps against the *original* file, not reset to 0 at the
seek point; and (2) silencedetect emits a matching `silence_end` even
for a silence interval that runs all the way to the end of the
requested range, rather than leaving it unmatched - so trailing silence
is identified by a detected interval's *end* landing at/near the
requested `end_s`, not by an unpaired `silence_start`.

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


def trim_leading_trailing_silence(
    source_path: str,
    *,
    start_s: float,
    end_s: float,
    noise_threshold_db: float = _DEFAULT_NOISE_THRESHOLD_DB,
    min_silence_duration_s: float = _DEFAULT_MIN_SILENCE_DURATION_S,
    max_trim_s: float = _DEFAULT_MAX_TRIM_S,
) -> tuple[float, float]:
    duration = end_s - start_s
    if duration <= 0 or not Path(source_path).exists():
        return start_s, end_s

    try:
        import imageio_ffmpeg
    except ImportError:
        return start_s, end_s

    try:
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run(
            [
                ffmpeg_bin,
                "-ss", str(start_s),
                "-i", source_path,
                "-t", str(duration),
                "-af", f"silencedetect=noise={noise_threshold_db}dB:d={min_silence_duration_s}",
                "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            timeout=_DETECTION_TIMEOUT_S,
        )
    except Exception:
        logger.warning("silence_detection_failed", source_path=source_path, exc_info=True)
        return start_s, end_s

    starts = [float(m) for m in _SILENCE_START_RE.findall(result.stderr)]
    ends = [float(m) for m in _SILENCE_END_RE.findall(result.stderr)]
    pairs = list(zip(starts, ends))

    trimmed_start, trimmed_end = start_s, end_s

    if pairs and abs(pairs[0][0] - start_s) <= _BOUNDARY_MATCH_TOLERANCE_S:
        trimmed_start = min(start_s + max_trim_s, pairs[0][1])

    if pairs and pairs[-1][1] >= end_s - _BOUNDARY_MATCH_TOLERANCE_S:
        trimmed_end = max(end_s - max_trim_s, pairs[-1][0])

    if trimmed_end - trimmed_start < _MIN_RESULTING_DURATION_S:
        return start_s, end_s

    logger.info(
        "silence_trimmed",
        source_path=source_path,
        original=(start_s, end_s),
        trimmed=(trimmed_start, trimmed_end),
    )
    return trimmed_start, trimmed_end
