"""Automated QC (ARCHITECTURE.md §7.1's "automated QC report": duration
match, audio sync, caption accuracy).

**v1.1 fix (PHASE1_AUDIT.md F4):** this replaces the previous hardcoded
`video.qc_status = "passed"`, which was set unconditionally after every
successful render regardless of what actually happened — a video with a
missing audio file or wildly mismatched duration would have shown
"passed" to a reviewer with no real check behind it.

This is deliberately a set of cheap, structural checks, not a perceptual
content-quality model — that's Phase 2+ territory (ARCHITECTURE.md §6b's
retention-prediction score). What's verified here is that the pipeline did
what it claims to have done: the audio file exists and is non-empty,
captions were generated and roughly cover the audio, the rendered duration
is plausible relative to the script's target, and the render asset itself
exists on disk (for local-file backends).
"""

from dataclasses import dataclass
from pathlib import Path

from content_factory.db.models.content import Script
from content_factory.video_production.captions import CaptionCue
from content_factory.video_production.renderer.base import RenderResult
from content_factory.video_production.tts.base import TTSResult

# A render is allowed to land within +/- this fraction of the script's
# requested target_duration_s before being flagged. Generous on purpose —
# TTS pacing is naturally variable — but tight enough to catch a genuinely
# broken render (e.g. a near-zero-length or wildly-inflated asset).
DURATION_TOLERANCE_RATIO = 0.5

# Captions should cover at least this fraction of the audio's own duration;
# anything less suggests caption generation was cut short or misaligned.
MIN_CAPTION_COVERAGE_RATIO = 0.5


@dataclass(frozen=True)
class QCResult:
    passed: bool
    checks: dict[str, bool]
    notes: str


def run_automated_qc(
    *,
    script: Script,
    tts_result: TTSResult,
    render_result: RenderResult,
    captions: list[CaptionCue],
) -> QCResult:
    checks: dict[str, bool] = {}
    failures: list[str] = []

    audio_path = Path(tts_result.audio_path) if tts_result.audio_path else None
    audio_ok = bool(audio_path and audio_path.exists() and audio_path.stat().st_size > 0)
    checks["audio_present"] = audio_ok
    if not audio_ok:
        failures.append(f"voiceover audio file missing or empty ({tts_result.audio_path!r})")

    captions_ok = bool(captions)
    if not captions_ok:
        failures.append("no captions were generated")
    elif tts_result.duration_s > 0:
        coverage_ratio = captions[-1].end_s / tts_result.duration_s
        captions_ok = coverage_ratio >= MIN_CAPTION_COVERAGE_RATIO
        if not captions_ok:
            failures.append(
                f"captions cover only {coverage_ratio:.0%} of the {tts_result.duration_s}s audio "
                f"(minimum {MIN_CAPTION_COVERAGE_RATIO:.0%})"
            )
    checks["captions_cover_audio"] = captions_ok

    duration_ok = True
    if script.target_duration_s:
        lower = script.target_duration_s * (1 - DURATION_TOLERANCE_RATIO)
        upper = script.target_duration_s * (1 + DURATION_TOLERANCE_RATIO)
        duration_ok = lower <= render_result.duration_s <= upper
        if not duration_ok:
            failures.append(
                f"rendered duration {render_result.duration_s}s is outside the expected "
                f"range [{lower:.1f}s, {upper:.1f}s] for a {script.target_duration_s}s target"
            )
    checks["duration_within_tolerance"] = duration_ok

    asset_ok = True
    if render_result.asset_url and not render_result.asset_url.startswith(("http://", "https://")):
        asset_ok = Path(render_result.asset_url).exists()
        if not asset_ok:
            failures.append(f"rendered asset not found on disk ({render_result.asset_url!r})")
    checks["render_asset_present"] = asset_ok

    passed = all(checks.values())
    notes = "; ".join(failures) if failures else "all automated checks passed"
    return QCResult(passed=passed, checks=checks, notes=notes)
