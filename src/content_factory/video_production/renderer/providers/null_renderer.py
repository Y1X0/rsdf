"""Default render backend: writes a JSON render manifest instead of an
actual video file. This is the production-safe default in any environment
without ffmpeg/Pillow installed (this sandbox included — see docs/PHASE1.md),
and it's what the test suite always uses so tests never depend on a real
rendering toolchain. It fully exercises the pipeline's data flow (Script ->
TTS -> captions -> "rendered asset" -> Video row) end to end; only the
actual pixels are stubbed.
"""

import json
import time
from pathlib import Path

from content_factory.video_production.renderer.base import RenderRequest, RenderResult, VideoRenderer


class NullRenderer(VideoRenderer):
    def __init__(self, storage_dir: Path) -> None:
        self._storage_dir = storage_dir

    def render(self, request: RenderRequest) -> RenderResult:
        start = time.monotonic()
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "video_id": request.video_id,
            "template_id": request.template_id,
            "hook_text": request.hook_text,
            "script_text": request.script_text,
            "voiceover_audio_path": request.voiceover_audio_path,
            "captions": [
                {"text": c.text, "start_s": c.start_s, "end_s": c.end_s} for c in request.captions
            ],
        }
        manifest_path = self._storage_dir / f"video_{request.video_id}_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        duration_s = request.target_duration_s or (
            request.captions[-1].end_s if request.captions else 15.0
        )
        duration_ms = max(int((time.monotonic() - start) * 1000), 1)

        return RenderResult(
            asset_url=str(manifest_path),
            duration_s=duration_s,
            provider="null",
            model="manifest-only",
            model_version="v1",
            cost_usd=0.0,
            duration_ms=duration_ms,
        )
