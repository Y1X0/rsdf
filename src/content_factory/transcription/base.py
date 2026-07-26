"""The one interface business logic is allowed to depend on for turning a
source video's audio into a timestamped transcript — same shape as
llm/base.py's LLMClient: a provider interface, a factory, a zero-dependency
safe default, and one real implementation behind a lazy import.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    provider: str = "null"
    model: str = "null"
    duration_s: float | None = None
    cost_usd: float = 0.0
    duration_ms: int = 0


class TranscriptionProvider(ABC):
    @abstractmethod
    def transcribe(self, audio_path: str) -> TranscriptionResult:
        """Transcribe the audio track of the file at `audio_path` (a local
        path — the source video itself, most audio/video containers work
        directly with real providers) and return the full text plus a
        timestamped segment breakdown."""
        raise NotImplementedError
