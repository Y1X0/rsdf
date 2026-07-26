"""Zero-dependency safe default — used when TRANSCRIPTION_PROVIDER is
unset or its API key is missing (config.Settings.resolved_transcription_provider),
the same "degrade safely, never crash" contract as FakeLLMClient and
NullRenderer. Returns a clearly-labeled empty result rather than a real
transcript, so ClipSelectionAgent has nothing to work with and the
resulting empty clip list is an honest reflection of "not configured," not
a silently fabricated one.
"""

from content_factory.transcription.base import TranscriptionProvider, TranscriptionResult


class NullTranscriptionProvider(TranscriptionProvider):
    def transcribe(self, audio_path: str) -> TranscriptionResult:
        return TranscriptionResult(text="", segments=[], provider="null", model="null")
