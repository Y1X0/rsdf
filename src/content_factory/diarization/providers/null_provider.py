"""Zero-dependency safe default - used when DIARIZATION_PROVIDER is unset
or its dependencies aren't configured (config.Settings.resolved_diarization_provider),
the same "degrade safely, never crash" contract as NullTranscriptionProvider.
Assumes a single speaker for the whole clip - an honest reflection of "not
configured," not a fabricated multi-speaker breakdown - and is also, today,
the *correct* choice for this app's current free-tier hosting: real
diarization's PyTorch-based models need meaningfully more RAM/CPU than this
container has been shown to survive (see providers/pyannote_provider.py's
own docstring for the measured history behind that statement).
"""

from content_factory.diarization.base import DiarizationResult, SpeakerDiarizationProvider


class NullDiarizationProvider(SpeakerDiarizationProvider):
    def diarize(self, audio_path: str) -> DiarizationResult:
        return DiarizationResult(turns=[], speaker_count=1, provider="null")
