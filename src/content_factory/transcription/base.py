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
class TranscriptWord:
    """Word-level timing, finer-grained than TranscriptSegment (a segment
    is typically a whole sentence/phrase, several seconds long). Lets a
    caption renderer put text on screen in close sync with the exact
    moment each word is actually spoken, rather than showing a whole
    sentence for its entire multi-second segment window. Optional: a
    provider that can't supply it (or a source video transcribed before
    this existed) leaves `TranscriptionResult.words` empty, and callers
    fall back to segment-level timing - never a hard requirement."""

    start_s: float
    end_s: float
    word: str


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    words: list[TranscriptWord] = field(default_factory=list)
    provider: str = "null"
    model: str = "null"
    duration_s: float | None = None
    cost_usd: float = 0.0
    duration_ms: int = 0


class TranscriptionProvider(ABC):
    @abstractmethod
    def transcribe(self, audio_path: str) -> TranscriptionResult:
        """Transcribe the audio track of the file at `audio_path` (a local
        path — most audio/video containers work directly with real
        providers) and return the full text plus a timestamped segment
        breakdown. Callers going through clip_service.transcribe_source_video
        typically pass a small, pre-extracted audio-only file rather than
        the original source video directly — see
        transcription/audio_extraction.py — since a real provider's request
        size limit is easy for a genuinely long recording's raw file to
        exceed; providers should not assume `audio_path` is the original
        upload."""
        raise NotImplementedError
