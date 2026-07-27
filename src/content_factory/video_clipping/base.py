"""ClipRenderer — the montage-pipeline equivalent of
video_production/renderer/base.py's VideoRenderer: business logic
(services/clip_service.py) depends only on this interface, never on
ffmpeg/imageio-ffmpeg directly, and never even knows which concrete
implementation is in use — decided once, in factory.py, from
configuration.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from content_factory.transcription.base import TranscriptSegment, TranscriptWord


@dataclass(frozen=True)
class ClipRenderRequest:
    clip_id: int
    source_path: str
    start_s: float
    end_s: float
    hook_text: str | None
    transcript_segments: list[TranscriptSegment] = field(default_factory=list)
    # Optional, finer-grained alternative to transcript_segments - when
    # present, a renderer can burn captions synced to each word's own
    # timing instead of showing a whole segment's text for its entire
    # (often several-second) window. Empty by default so every existing
    # caller/renderer that doesn't know about this yet keeps working
    # unchanged on segment-level timing.
    transcript_words: list[TranscriptWord] = field(default_factory=list)


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
