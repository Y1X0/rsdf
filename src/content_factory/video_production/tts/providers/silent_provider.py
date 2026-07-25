"""Default TTS backend: produces a real (silent) WAV file using only the
Python standard library, so the production pipeline is fully exercisable
with zero external dependencies and zero API keys. Word timings are
approximated evenly across the estimated speaking duration (see
video_production/captions.even_word_timings) — this is the documented
Phase 1 simplification in place of real per-word alignment.
"""

import time
import wave
from pathlib import Path

from content_factory.video_production.captions import estimate_duration_s, even_word_timings
from content_factory.video_production.tts.base import TTSProvider, TTSResult

_SAMPLE_RATE = 16_000


class SilentTTSProvider(TTSProvider):
    def __init__(self, storage_dir: Path) -> None:
        self._storage_dir = storage_dir

    def synthesize(self, *, text: str, voice_id: str) -> TTSResult:
        start = time.monotonic()
        duration_s = estimate_duration_s(text)
        word_timings = even_word_timings(text, duration_s)

        self._storage_dir.mkdir(parents=True, exist_ok=True)
        audio_path = self._storage_dir / f"silent_{abs(hash(text)) % 10_000_000}.wav"
        n_frames = int(duration_s * _SAMPLE_RATE)
        with wave.open(str(audio_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(_SAMPLE_RATE)
            wav_file.writeframes(b"\x00\x00" * n_frames)

        duration_ms = max(int((time.monotonic() - start) * 1000), 1)
        return TTSResult(
            audio_path=str(audio_path),
            duration_s=duration_s,
            provider="silent",
            model="silent-placeholder",
            model_version="v1",
            cost_usd=0.0,
            duration_ms=duration_ms,
            word_timings=word_timings,
        )
