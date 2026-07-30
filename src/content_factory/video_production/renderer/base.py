"""VideoRenderer — the generic abstraction adjustment #2 asked for.

`services/production_service.py` (business logic) depends only on
`VideoRenderer`, `RenderRequest`, and `RenderResult` from this module. It
never imports Pillow, ffmpeg, Remotion, Runway, or Kling directly, and it
never even knows which concrete renderer is in use — that's decided once,
in `factory.py`, from configuration. Adding a Remotion-, Runway-, or
Kling-backed renderer later means writing one new class in
`providers/` and adding one branch to `factory.py`; nothing in
`production_service.py` changes.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from content_factory.video_production.captions import CaptionCue


@dataclass(frozen=True)
class RenderRequest:
    video_id: int
    template_id: str
    hook_text: str
    script_text: str
    voiceover_audio_path: str | None
    captions: list[CaptionCue] = field(default_factory=list)
    target_duration_s: float | None = None


@dataclass(frozen=True)
class RenderResult:
    asset_url: str
    duration_s: float
    provider: str
    thumbnail_url: str | None = None
    model: str | None = None
    model_version: str | None = None
    cost_usd: float = 0.0
    duration_ms: int = 0


class VideoRenderer(ABC):
    @abstractmethod
    def render(self, request: RenderRequest) -> RenderResult:
        raise NotImplementedError
