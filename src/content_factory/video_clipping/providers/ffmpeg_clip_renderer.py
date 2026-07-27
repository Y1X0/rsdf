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

Captions prefer word-level timing (`request.transcript_words`) over
segment-level (`request.transcript_segments`) when both are available:
a segment is typically a whole sentence, several seconds long, so
captioning at segment granularity shows a wall of text for its entire
window rather than text appearing in sync with what's actually being
said at that instant. Word-level cues are grouped a few words at a time
using each word's own real start/end time, which is what actually puts
text on screen "at the same time the person is speaking" rather than a
whole sentence early or late relative to the audio. Falls back to
segment-level whenever word-level timing isn't available (an older
transcript, or a transcription provider that couldn't supply it) - never
a hard requirement.
"""

import subprocess
import tempfile
import time
from pathlib import Path

from content_factory.transcription.base import TranscriptSegment, TranscriptWord
from content_factory.video_clipping.base import ClipRenderer, ClipRenderRequest, ClipRenderResult

# 9:16 short-form. Matches TemplatePillowRenderer's own reduced size and
# for the identical, measured reason: a real production incident showed
# the container crashing outright under a free-hosting-tier memory limit
# while encoding at full 1080x1920 with ffmpeg's default preset - this
# renderer decodes a real, potentially much larger source video on top of
# that, so it is at least as exposed to the same failure mode.
_FRAME_SIZE = (540, 960)
_HOOK_DURATION_S = 3.0

# Word-grouping for tightly-synced captions: break a cue early on a
# natural pause between words (a real gap in the word-level timing, not
# a guess) or after this many words, whichever comes first. Small enough
# to read comfortably on a 9:16 frame while staying close to real-time.
_MAX_WORDS_PER_CUE = 4
_WORD_PAUSE_BREAK_S = 0.6
# Real Whisper timestamps are floats with the usual binary-floating-point
# representation noise (e.g. 2.0 - 1.4 == 0.6000000000000001, not exactly
# 0.6) - without this tolerance, a gap that's genuinely "exactly at the
# threshold" could non-deterministically trigger a break depending on
# which side of that noise it lands on.
_PAUSE_EPSILON_S = 1e-6


def _format_srt_timestamp(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    millis = int(round((secs - int(secs)) * 1000))
    return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d},{millis:03d}"


def _group_words_into_cues(words: list[TranscriptWord]) -> list[tuple[float, float, str]]:
    """`words` must already be clip-relative (0-based) timing. Groups
    consecutive words into short cues using their own real start/end
    times - the cue's start/end is the first/last word's own timing, not
    an estimate."""
    cues: list[tuple[float, float, str]] = []
    current: list[TranscriptWord] = []
    for w in words:
        if current and (
            w.start_s - current[-1].end_s > _WORD_PAUSE_BREAK_S + _PAUSE_EPSILON_S
            or len(current) >= _MAX_WORDS_PER_CUE
        ):
            cues.append((current[0].start_s, current[-1].end_s, " ".join(x.word for x in current)))
            current = []
        current.append(w)
    if current:
        cues.append((current[0].start_s, current[-1].end_s, " ".join(x.word for x in current)))
    return cues


def _build_srt(
    *,
    hook_text: str | None,
    segments: list[TranscriptSegment],
    words: list[TranscriptWord],
    start_s: float,
    end_s: float,
) -> str:
    cues: list[tuple[float, float, str]] = []
    hook_end_s = 0.0
    if hook_text:
        hook_end_s = min(_HOOK_DURATION_S, end_s - start_s)
        cues.append((0.0, hook_end_s, hook_text))

    # Only words that actually overlap this clip's own [start_s, end_s]
    # window, shifted onto the clip's own 0-based timeline - and never
    # starting before the hook cue ends, so the two never visually overlap.
    overlapping_words = [
        TranscriptWord(start_s=max(w.start_s, start_s) - start_s, end_s=min(w.end_s, end_s) - start_s, word=w.word)
        for w in words
        if min(w.end_s, end_s) > max(w.start_s, start_s)
    ]
    overlapping_words = [w for w in overlapping_words if w.start_s >= hook_end_s]

    if overlapping_words:
        cues.extend(_group_words_into_cues(overlapping_words))
    else:
        # No word-level timing available for this clip - fall back to
        # segment-level captions exactly as before word-level existed.
        for seg in segments:
            overlap_start = max(seg.start_s, start_s)
            overlap_end = min(seg.end_s, end_s)
            if overlap_end <= overlap_start:
                continue
            seg_rel_start = max(overlap_start - start_s, hook_end_s)
            seg_rel_end = overlap_end - start_s
            if seg_rel_end <= seg_rel_start:
                continue
            cues.append((seg_rel_start, seg_rel_end, seg.text))

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
            words=request.transcript_words,
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
                # Same memory-reduction pair as TemplatePillowRenderer, same
                # measured reasoning: ultrafast cuts libx264's own buffers
                # substantially; a bounded thread count avoids the encoder
                # oversubscribing a tiny, memory-constrained host.
                "-preset", "ultrafast",
                "-threads", "1",
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
