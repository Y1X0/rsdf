from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from content_factory.video_production.captions import WordTiming


@dataclass(frozen=True)
class TTSResult:
    audio_path: str
    duration_s: float
    provider: str
    model: str | None
    model_version: str | None
    cost_usd: float
    duration_ms: int
    word_timings: list[WordTiming] = field(default_factory=list)


class TTSProvider(ABC):
    @abstractmethod
    def synthesize(self, *, text: str, voice_id: str) -> TTSResult:
        raise NotImplementedError
