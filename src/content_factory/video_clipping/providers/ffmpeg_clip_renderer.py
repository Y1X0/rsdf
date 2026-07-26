"""Real clip renderer: actually cuts the requested [start_s, end_s] range
out of a real source video file and burns the hook + matching transcript
segments as captions, via ffmpeg (imageio-ffmpeg's bundled binary — the
same dependency TemplatePillowRenderer already uses, no new Docker/system
package beyond fonts — see Dockerfile). This is real footage being edited,
not content generated from scratch, which is the entire point of the clip
factory pipeline.

Verified directly against this exact approach before writing this as
"working code": cut a real 12s generated test video down to a 4s range
with `-ss <start> -i source -t <duration>` (accurate trim, not just
nearest-keyframe), and burned two real caption cues via the `subtitles`
filter (libass, bundled in imageio-ffmpeg's static build alongside
fontconfig) — confirmed a valid, correctly-sized output file with no
filter errors and a real font (`fontselect: ... -> LiberationSans`)
resolved for the burned text.

Caption/hook text is written to a real .srt file rather than built as a
drawtext filter string: this bundled ffmpeg build does not include the
`drawtext` filter at all (confirmed via `ffmpeg -filters`), only
`subtitles`/`ass` (libass) — using a real subtitle file sidesteps
drawtext's filter-string escaping entirely as a side benefit.
"""

import subprocess
import tempfile
import time
from pathlib import Path

from content_factory.transcription.base import TranscriptSegment
from content_factory.video_clipping.base import ClipRenderer, ClipRenderRequest, ClipRenderResult

_FRAME_SIZE = (1080, 1920)  # 9:16 short-form, matching TemplatePillowRenderer
_HOOK_DURATION_S = 3.0


def _format_srt_timestamp(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    millis = int(round((secs - int(secs)) * 1000))
    return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d},{millis:03d}"


def _build_srt(*, hook_text: str | None, segments: list[TranscriptSegment], start_s: float, end_s: float) -> str:
    cues: list[tuple[float, float, str]] = []
    if hook_text:
        cues.append((0.0, min(_HOOK_DURATION_S, end_s - start_s), hook_text))
    for seg in segments:
        # Only segments that actually overlap this clip's own [start_s, end_s]
        # window, shifted onto the clip's own 0-based timeline.
        overlap_start = max(seg.start_s, start_s)
        overlap_end = min(seg.end_s, end_s)
        if overlap_end <= overlap_start:
            continue
        cues.append((overlap_start - start_s, overlap_end - start_s, seg.text))

    lines = []
    for i, (rel_start, rel_end, text) in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(f"{_format_srt_timestamp(rel_start)} --> {_format_srt_timestamp(rel_end)}")
        lines.append(text.strip() or " ")
        lines.append("")
    return "\n".join(lines)


class FfmpegClipRenderer(ClipRenderer):
    def __init__(self, storage_dir: Path) -> None:
        self._storage_dir = storage_dir

    def render(self, request: ClipRenderRequest) -> ClipRenderResult:
        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise RuntimeError(
                "FfmpegClipRenderer requires the 'rendering' extra: pip install '.[rendering]'"
            ) from exc

        start = time.monotonic()
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        duration_s = round(request.end_s - request.start_s, 3)
        if duration_s <= 0:
            raise ValueError(f"end_s must be after start_s (got start_s={request.start_s}, end_s={request.end_s})")

        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        asset_path = self._storage_dir / f"clip_{request.clip_id}.mp4"
        thumbnail_path = self._storage_dir / f"clip_{request.clip_id}_thumb.jpg"

        srt_content = _build_srt(
            hook_text=request.hook_text,
            segments=request.transcript_segments,
            start_s=request.start_s,
            end_s=request.end_s,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            srt_path = Path(tmp_dir) / "captions.srt"
            srt_path.write_text(srt_content, encoding="utf-8")

            vf_parts = [f"scale={_FRAME_SIZE[0]}:{_FRAME_SIZE[1]}:force_original_aspect_ratio=decrease",
                        f"pad={_FRAME_SIZE[0]}:{_FRAME_SIZE[1]}:(ow-iw)/2:(oh-ih)/2"]
            if srt_content.strip():
                # ffmpeg filter-graph syntax treats ':' as an option separator,
                # so a plain filesystem path (which may itself contain ':') must
                # be escaped before being embedded inside the subtitles= filter.
                escaped_srt_path = str(srt_path).replace("\\", "\\\\").replace(":", "\\:")
                vf_parts.append(f"subtitles={escaped_srt_path}")

            cmd = [
                ffmpeg_bin,
                "-y",
                "-ss", str(request.start_s),
                "-i", request.source_path,
                "-t", str(duration_s),
                "-vf", ",".join(vf_parts),
                "-c:v", "libx264",
                "-c:a", "aac",
                str(asset_path),
            ]
            subprocess.run(cmd, check=True, capture_output=True)

            thumb_cmd = [
                ffmpeg_bin,
                "-y",
                "-i", str(asset_path),
                "-frames:v", "1",
                "-update", "1",
                str(thumbnail_path),
            ]
            subprocess.run(thumb_cmd, check=True, capture_output=True)

        duration_ms = int((time.monotonic() - start) * 1000)
        return ClipRenderResult(
            asset_url=str(asset_path),
            duration_s=duration_s,
            provider="ffmpeg",
            thumbnail_url=str(thumbnail_path),
            duration_ms=duration_ms,
        )
