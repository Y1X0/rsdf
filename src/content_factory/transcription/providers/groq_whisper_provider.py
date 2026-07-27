"""Groq Whisper transcription — real speech-to-text for the clip factory's
"understand the source video" step. Same OpenAI-compatible REST shape and
lazy-httpx-import convention as llm/providers/groq_provider.py (this
project's other real Groq integration): no new SDK dependency, just the
"groq" extra's httpx.

Endpoint: POST https://api.groq.com/openai/v1/audio/transcriptions
(multipart/form-data: file + model + response_format=verbose_json), which
returns a top-level `text` plus a `segments` list of
{start, end, text, ...} — the timestamped breakdown ClipSelectionAgent
needs to pick moments out of the source video's timeline.
"""

import time
from pathlib import Path

from content_factory.transcription.base import (
    TranscriptionProvider,
    TranscriptionResult,
    TranscriptSegment,
    TranscriptWord,
)

_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# Illustrative per-minute-of-audio pricing, USD, for whisper-large-v3 on
# Groq's pay-as-you-go tier — same "plain constant, easy to update"
# approach as groq_provider.py's own pricing constant. Verify against
# Groq's current pricing page before relying on this for anything beyond
# illustrative Cost Control Layer tracking.
_PRICE_PER_MINUTE_USD = 0.04


class GroqWhisperProvider(TranscriptionProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def transcribe(self, audio_path: str) -> TranscriptionResult:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "GroqWhisperProvider requires the 'groq' extra: pip install '.[groq]'"
            ) from exc

        start = time.monotonic()
        path = Path(audio_path)
        with path.open("rb") as f:
            response = httpx.post(
                _TRANSCRIPTIONS_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                # A plain dict can only hold one value per key, but
                # requesting both granularities means sending
                # "timestamp_granularities[]" twice - a list of tuples is
                # httpx's (and requests') way of encoding repeated form
                # fields. Both explicitly requested (rather than relying on
                # segment-level being some implicit default) so the
                # response's `words` array - real per-word timing, not
                # something derived after the fact - is never silently
                # missing depending on the API's own default behavior.
                data=[
                    ("model", self._model),
                    ("response_format", "verbose_json"),
                    ("timestamp_granularities[]", "segment"),
                    ("timestamp_granularities[]", "word"),
                ],
                files={"file": (path.name, f, "application/octet-stream")},
                timeout=300.0,
            )
        duration_ms = int((time.monotonic() - start) * 1000)
        response.raise_for_status()
        payload = response.json()

        text = payload.get("text", "") or ""
        segments = [
            TranscriptSegment(start_s=float(s["start"]), end_s=float(s["end"]), text=s.get("text", "").strip())
            for s in payload.get("segments", [])
        ]
        # Word-level timing is a genuine best-effort extra, not a hard
        # requirement of a valid response: an older/different model, or an
        # API that only honors "segment", can validly omit "words" or
        # include an item missing a field - skip just that malformed item
        # rather than letting one bad entry blank out every real word
        # timestamp that did come back.
        words: list[TranscriptWord] = []
        for w in payload.get("words", []):
            try:
                words.append(TranscriptWord(start_s=float(w["start"]), end_s=float(w["end"]), word=str(w["word"]).strip()))
            except (KeyError, TypeError, ValueError):
                continue
        duration_s = payload.get("duration")
        cost_usd = (duration_s / 60.0 * _PRICE_PER_MINUTE_USD) if duration_s else 0.0

        return TranscriptionResult(
            text=text,
            segments=segments,
            words=words,
            provider="groq",
            model=self._model,
            duration_s=float(duration_s) if duration_s else None,
            cost_usd=round(cost_usd, 6),
            duration_ms=duration_ms,
        )
