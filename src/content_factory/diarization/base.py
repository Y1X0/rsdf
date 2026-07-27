"""The one interface business logic is allowed to depend on for figuring
out *who* is talking during each stretch of a source video's audio - same
shape as transcription/base.py: a provider interface, a factory, a
zero-dependency safe default, and one real (heavy, optional) implementation
behind a lazy import.

Diarization is a separate concern from transcription (word-level timing
answers "when was this word said", diarization answers "who said it") -
kept as its own small interface rather than folded into
TranscriptionProvider so a deployment can run real Whisper transcription
without also paying for real diarization's much heavier compute cost, or
vice versa.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SpeakerTurn:
    start_s: float
    end_s: float
    speaker_label: str


@dataclass(frozen=True)
class DiarizationResult:
    turns: list[SpeakerTurn] = field(default_factory=list)
    speaker_count: int = 1
    provider: str = "null"
    duration_ms: int = 0


class SpeakerDiarizationProvider(ABC):
    @abstractmethod
    def diarize(self, audio_path: str) -> DiarizationResult:
        """Identify speaker turns in the audio track of the file at
        `audio_path`. `speaker_count` of 1 (the safe default) means either
        a real single-speaker recording or "not actually diarized" -
        callers that care about the distinction check `provider`."""
        raise NotImplementedError
