"""ClipRenderer — the montage-pipeline equivalent of
video_production/renderer/base.py's VideoRenderer: business logic
(services/clip_service.py) depends only on this interface, never on
ffmpeg/imageio-ffmpeg directly, and never even knows which concrete
implementation is in use — decided once, in factory.py, from
configuration.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from content_factory.transcription.base import TranscriptSegment


@dataclass(frozen=True)
class ClipRenderRequest:
    clip_id: int
    source_path: str
    start_s: float
    end_s: float
    hook_text: str | None
    transcript_segments: list[TranscriptSegment] = field(default_factory=list)


@dataclass(frozen=True)
class ClipRenderResult:
    asset_url: str
    duration_s: float
    provider: str
    thumbnail_url: str | None = None
    duration_ms: int = 0


class ClipRenderer(ABC):
    @abstractmethod
    def render(self, request: ClipRenderRequest) -> ClipRenderResult:
        raise NotImplementedError
