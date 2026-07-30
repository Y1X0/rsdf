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

Caption/hook text is written to a real .ass (Advanced SubStation Alpha)
file rather than built as a drawtext filter string: this bundled ffmpeg
build does not include the `drawtext` filter at all (confirmed via
`ffmpeg -filters`), only `subtitles`/`ass` (libass) — using a real
subtitle file sidesteps drawtext's filter-string escaping entirely as a
side benefit, and ASS specifically (rather than the simpler SRT) is what
makes the karaoke word-highlight below possible at all.

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

Captions are written as ASS (Advanced SubStation Alpha), not SRT: ASS
supports per-syllable `\\k` karaoke tags, which libass renders as a
genuine word-by-word highlight sweeping across each cue in sync with
speech, rather than a plain static line appearing/disappearing as a
whole block. This is the concrete mechanism for "text appears at
exactly the same time the person is speaking" beyond just grouping
cues by word timing. Verified directly (not assumed) against this exact
bundled ffmpeg/libass build with a real `\\k`-tagged .ass file before
writing this as "working code": renders with no filter errors and a
real font resolved for the burned text, same verification bar as the
original SRT-based approach this replaces. Hook text and segment-level
fallback captions have no per-word timing to key a sweep off, so they
render as plain (non-karaoke) lines, forced to a fixed white colour via
an explicit `\\c` override so they read the same as before this change.
"""

import subprocess
import tempfile
import time
from pathlib import Path

from content_factory.diarization.base import SpeakerTurn
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


def _format_ass_timestamp(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    centis = int(round((secs - int(secs)) * 100))
    if centis >= 100:
        # Rounding a value like 59.999s up to the next whole second - carry
        # it rather than emit an invalid "60" in the centisecond field.
        centis = 0
        secs += 1
    return f"{int(hours)}:{int(minutes):02d}:{int(secs):02d}.{centis:02d}"


def _escape_ass_text(text: str) -> str:
    """Strip characters that would either break ASS override-tag syntax
    (`{`/`}`) or a single-line Dialogue entry (`\\n`) - the same
    "sanitize before embedding in a subtitle file" concern the original
    SRT path already had, just for ASS's own syntax instead."""
    return text.replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ").strip()


def _group_words(words: list[TranscriptWord]) -> list[list[TranscriptWord]]:
    """`words` must already be clip-relative (0-based) timing. Groups
    consecutive words into short cues using their own real start/end
    times - a cue's start/end is the first/last word's own timing, not
    an estimate."""
    groups: list[list[TranscriptWord]] = []
    current: list[TranscriptWord] = []
    for w in words:
        if current and (
            w.start_s - current[-1].end_s > _WORD_PAUSE_BREAK_S + _PAUSE_EPSILON_S
            or len(current) >= _MAX_WORDS_PER_CUE
        ):
            groups.append(current)
            current = []
        current.append(w)
    if current:
        groups.append(current)
    return groups


def _group_words_into_cues(words: list[TranscriptWord]) -> list[tuple[float, float, str]]:
    return [(g[0].start_s, g[-1].end_s, " ".join(x.word for x in g)) for g in _group_words(words)]


def _karaoke_ass_text(words: list[TranscriptWord]) -> str:
    """Builds an ASS `\\k`-tagged line: each word's own highlight duration
    (in centiseconds) runs until the *next* word starts (so a natural
    pause between two words within the same cue is absorbed into the
    first word's highlight rather than left as a dead gap), except the
    last word, whose duration is just its own end-start. libass then
    sweeps the style's Primary/Secondary colours across the line in sync
    with these real per-word times - not an estimate or an even split."""
    parts = []
    for i, w in enumerate(words):
        duration_s = (words[i + 1].start_s - w.start_s) if i + 1 < len(words) else (w.end_s - w.start_s)
        centis = max(int(round(duration_s * 100)), 1)
        parts.append(f"{{\\k{centis}}}{_escape_ass_text(w.word)} ")
    return "".join(parts).strip()


_ASS_HEADER_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,LiberationSans,44,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,1,2,10,10,60,1
{speaker_styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# Non-karaoke lines (hook, segment-level fallback) have no per-word timing
# to sweep a highlight across, so they're forced to plain white via an
# explicit override rather than inheriting the style's yellow
# PrimaryColour (which, absent a \\k tag, is what plain text renders in) -
# keeps them visually identical to how they looked before this change.
_PLAIN_WHITE_OVERRIDE = r"{\c&H00FFFFFF&}"

# Named styles for distinguishing speakers when real diarization data is
# available (>= 2 distinct speaker labels actually overlap the clip) -
# each gets its own PrimaryColour (the karaoke "already spoken" colour),
# cycling through this palette if there are ever more speakers than
# colours. "Default" (used whenever there's only one speaker, or no
# diarization data at all - the common case) keeps the original
# yellow-on-white scheme, so nothing visually changes for any clip that
# hasn't opted into real diarization.
_SPEAKER_STYLE_NAMES = ["Speaker0", "Speaker1", "Speaker2", "Speaker3"]
_SPEAKER_PRIMARY_COLOURS = ["&H0000FFFF", "&H00FFFF00", "&H00FF00FF", "&H0000FF00"]  # yellow, cyan, magenta, green


def _speaker_style_map(speaker_turns: list[SpeakerTurn]) -> dict[str, str]:
    """Assigns each distinct speaker label a style name, in first-seen
    order (deterministic). Empty (or single-speaker) input intentionally
    yields an empty map - callers treat that as "use Default"."""
    labels_in_order: list[str] = []
    for turn in speaker_turns:
        if turn.speaker_label not in labels_in_order:
            labels_in_order.append(turn.speaker_label)
    if len(labels_in_order) < 2:
        return {}
    return {label: _SPEAKER_STYLE_NAMES[i % len(_SPEAKER_STYLE_NAMES)] for i, label in enumerate(labels_in_order)}


def _dominant_speaker_label(abs_start_s: float, abs_end_s: float, speaker_turns: list[SpeakerTurn]) -> str | None:
    """Which speaker turn overlaps [abs_start_s, abs_end_s] the most -
    both arguments are *absolute* (original source-video) timestamps,
    matching how speaker_turns are stored, not clip-relative ones."""
    best_overlap_s, best_label = 0.0, None
    for turn in speaker_turns:
        overlap_s = min(turn.end_s, abs_end_s) - max(turn.start_s, abs_start_s)
        if overlap_s > best_overlap_s:
            best_overlap_s, best_label = overlap_s, turn.speaker_label
    return best_label


def _build_captions(
    *,
    hook_text: str | None,
    segments: list[TranscriptSegment],
    words: list[TranscriptWord],
    speaker_turns: list[SpeakerTurn],
    start_s: float,
    end_s: float,
) -> str:
    events: list[tuple[float, float, str, str]] = []
    hook_end_s = 0.0
    if hook_text:
        hook_end_s = min(_HOOK_DURATION_S, end_s - start_s)
        events.append((0.0, hook_end_s, "Default", _PLAIN_WHITE_OVERRIDE + _escape_ass_text(hook_text)))

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
        style_map = _speaker_style_map(speaker_turns)
        for group in _group_words(overlapping_words):
            style = "Default"
            if style_map:
                # Un-shift back to absolute time to match against
                # speaker_turns, which are stored in the source video's
                # own absolute timeline, not this clip's relative one.
                label = _dominant_speaker_label(group[0].start_s + start_s, group[-1].end_s + start_s, speaker_turns)
                style = style_map.get(label, "Default")
            events.append((group[0].start_s, group[-1].end_s, style, _karaoke_ass_text(group)))
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
            events.append(
                (seg_rel_start, seg_rel_end, "Default", _PLAIN_WHITE_OVERRIDE + _escape_ass_text(seg.text))
            )

    style_lines = "\n".join(
        f"Style: {name},LiberationSans,44,{colour},&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,1,2,10,10,60,1"
        for name, colour in zip(_SPEAKER_STYLE_NAMES, _SPEAKER_PRIMARY_COLOURS)
    )
    lines = [_ASS_HEADER_TEMPLATE.format(width=_FRAME_SIZE[0], height=_FRAME_SIZE[1], speaker_styles=style_lines)]
    for rel_start, rel_end, style, text in events:
        lines.append(
            f"Dialogue: 0,{_format_ass_timestamp(rel_start)},{_format_ass_timestamp(rel_end)},"
            f"{style},,0,0,0,,{text or ' '}"
        )
    return "\n".join(lines) + "\n"


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

        captions_content = _build_captions(
            hook_text=request.hook_text,
            segments=request.transcript_segments,
            words=request.transcript_words,
            speaker_turns=request.speaker_turns,
            start_s=request.start_s,
            end_s=request.end_s,
        )
        has_events = "Dialogue:" in captions_content

        with tempfile.TemporaryDirectory() as tmp_dir:
            captions_path = Path(tmp_dir) / "captions.ass"
            captions_path.write_text(captions_content, encoding="utf-8")

            # setsar=1 closes a real (if cosmetic) artifact found via a live
            # end-to-end run against a horizontal source: force_original_
            # aspect_ratio=decrease's intermediate scale can round to a
            # dimension ffmpeg can't express as exactly 9:16 in integer
            # pixels, so it compensates by writing a slightly non-1:1 SAR
            # into the output (e.g. DAR 76:135 instead of 9:16) even though
            # the pixel dimensions are already exactly correct. Never
            # visible and every platform re-processes uploads anyway, but
            # there's no reason to ship a portrait short with any non-square
            # pixel metadata at all.
            vf_parts = [f"scale={_FRAME_SIZE[0]}:{_FRAME_SIZE[1]}:force_original_aspect_ratio=decrease",
                        f"pad={_FRAME_SIZE[0]}:{_FRAME_SIZE[1]}:(ow-iw)/2:(oh-ih)/2",
                        "setsar=1"]
            if has_events:
                # ffmpeg filter-graph syntax treats ':' as an option separator,
                # so a plain filesystem path (which may itself contain ':') must
                # be escaped before being embedded inside the subtitles= filter.
                escaped_captions_path = str(captions_path).replace("\\", "\\\\").replace(":", "\\:")
                vf_parts.append(f"subtitles={escaped_captions_path}")

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
                # One-pass loudnorm (EBU R128), targeting -16 LUFS integrated
                # loudness - the widely-used streaming/podcast target, and a
                # sensible default for short-form social audio too. One-pass
                # rather than the more accurate two-pass measure-then-apply
                # form deliberately: two-pass needs a first full decode just
                # to measure loudness before the real encode even starts,
                # doubling this step's decode cost on the same
                # memory/CPU-constrained host that already required the
                # ultrafast/threads=1 settings above.
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
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
