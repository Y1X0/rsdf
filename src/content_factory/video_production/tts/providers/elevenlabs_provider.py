"""Real ElevenLabs TTS provider. `httpx` is only imported here, lazily, so
the core install has no hard dependency on it — install with
`pip install '.[elevenlabs]'` to use this provider. Selected automatically
by video_production/tts/factory.py only when TTS_PROVIDER=elevenlabs and a
real ELEVENLABS_API_KEY is configured (config.Settings.resolved_tts_provider
falls back to "silent" otherwise).
"""

import time
from pathlib import Path

from content_factory.retry import RetryableProviderError, call_with_retry
from content_factory.video_production.captions import even_word_timings
from content_factory.video_production.tts.base import TTSProvider, TTSResult

_API_URL_TEMPLATE = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
# Illustrative pricing — see ARCHITECTURE.md §18's "validate against current
# vendor pricing" note; update this constant when real pricing is confirmed.
_PRICE_PER_1K_CHARS_USD = 0.18


class ElevenLabsTTSProvider(TTSProvider):
    def __init__(self, api_key: str, storage_dir: Path) -> None:
        self._api_key = api_key
        self._storage_dir = storage_dir

    def synthesize(self, *, text: str, voice_id: str) -> TTSResult:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "ElevenLabsTTSProvider requires the 'elevenlabs' extra: "
                "pip install '.[elevenlabs]'"
            ) from exc

        def _call():
            try:
                response = httpx.post(
                    _API_URL_TEMPLATE.format(voice_id=voice_id),
                    headers={"xi-api-key": self._api_key},
                    json={"text": text},
                    timeout=60.0,
                )
            except httpx.TimeoutException as exc:
                raise RetryableProviderError(f"ElevenLabs request timed out: {exc}") from exc
            except httpx.RequestError as exc:
                raise RuntimeError(f"ElevenLabs request failed: {exc}") from exc
            if response.status_code >= 500:
                raise RetryableProviderError(f"ElevenLabs API returned {response.status_code}")
            return response

        start = time.monotonic()
        # Transient-only (5xx/timeout): a 4xx (bad key, invalid voice_id)
        # retrying would just waste attempts on an error retrying can't
        # fix - see content_factory/retry.py.
        response = call_with_retry(_call)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Same reasoning as groq_provider.py's own error wrapping: the
            # real response body is the only thing that explains *why*
            # wherever agent_runs.error_message ends up being the only
            # record of the failure.
            raise RuntimeError(f"ElevenLabs API error (HTTP {response.status_code}): {response.text}") from exc

        self._storage_dir.mkdir(parents=True, exist_ok=True)
        audio_path = self._storage_dir / f"elevenlabs_{abs(hash(text)) % 10_000_000}.mp3"
        audio_path.write_bytes(response.content)
        duration_ms = int((time.monotonic() - start) * 1000)

        # ElevenLabs' base API does not return word-level timestamps unless
        # using the separate "with timestamps" endpoint — approximate here
        # and note it as a known upgrade path (see captions.py docstring).
        duration_s = len(text.split()) / 2.5
        cost_usd = round(len(text) / 1000 * _PRICE_PER_1K_CHARS_USD, 6)

        return TTSResult(
            audio_path=str(audio_path),
            duration_s=round(duration_s, 2),
            provider="elevenlabs",
            model="eleven_multilingual_v2",
            model_version="v2",
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            word_timings=even_word_timings(text, duration_s),
        )
