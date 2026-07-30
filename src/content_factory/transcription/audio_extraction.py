"""Compact-audio extraction ahead of transcription.

Real hosted speech-to-text APIs (Groq's Whisper endpoint included) reject
requests above a real size limit — sending the raw long-form source video
(video stream and all) directly, as `audio_path`, works fine for a small
test clip and silently risks failing for exactly the genuinely long
recordings this pipeline exists to handle: a one-hour 1080p recording can
easily be several GB, almost certainly over any hosted Whisper-class API's
real per-request limit, while its audio track compressed down is a few MB.

Best-effort by design, matching every other optional ffmpeg-based step in
this codebase (scene detection, silence trim): if the "rendering" extra
isn't installed or ffmpeg itself fails for any reason, this returns None
and the caller falls back to transcribing the original file directly
(the previous behavior) rather than blocking the pipeline.
"""

import subprocess
import tempfile
from pathlib import Path

from content_factory.logging_config import get_logger

logger = get_logger(__name__)

_EXTRACTION_TIMEOUT_S = 600


def extract_compact_audio(source_path: str) -> str | None:
    """Returns a path to a new, small, mono 16kHz AAC file containing just
    `source_path`'s audio track — comfortably above what Whisper-class
    models need for accurate transcription, while cutting a typical
    multi-GB long-form video down to a few MB per hour of audio. Returns
    None if extraction wasn't possible; the caller owns cleanup of the
    returned path (and its parent directory) when one is returned."""
    try:
        import imageio_ffmpeg
    except ImportError:
        return None

    tmp_dir = Path(tempfile.mkdtemp(prefix="transcribe_audio_"))
    dest_path = tmp_dir / "audio.m4a"
    try:
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-i", source_path,
                "-vn",  # no video stream at all in the output - audio only
                "-ac", "1",
                "-ar", "16000",
                "-c:a", "aac",
                "-b:a", "64k",
                str(dest_path),
            ],
            check=True,
            capture_output=True,
            timeout=_EXTRACTION_TIMEOUT_S,
        )
        return str(dest_path)
    except Exception:
        logger.warning("audio_extraction_failed", source_path=source_path, exc_info=True)
        dest_path.unlink(missing_ok=True)
        tmp_dir.rmdir()
        return None


def cleanup_extracted_audio(extracted_path: str) -> None:
    path = Path(extracted_path)
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass
