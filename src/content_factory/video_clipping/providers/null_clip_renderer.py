"""Default clip render backend: writes a JSON manifest referencing the cut
range instead of actually cutting the source video — the same
production-safe default as video_production/renderer/providers/null_renderer.py,
for any environment without ffmpeg installed, and what the test suite
always uses.
"""

import json
import time
from pathlib import Path

from content_factory.video_clipping.base import ClipRenderer, ClipRenderRequest, ClipRenderResult


class NullClipRenderer(ClipRenderer):
    def __init__(self, storage_dir: Path) -> None:
        self._storage_dir = storage_dir

    def render(self, request: ClipRenderRequest) -> ClipRenderResult:
        start = time.monotonic()
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "clip_id": request.clip_id,
            "source_path": request.source_path,
            "start_s": request.start_s,
            "end_s": request.end_s,
            "hook_text": request.hook_text,
        }
        manifest_path = self._storage_dir / f"clip_{request.clip_id}_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        duration_ms = max(int((time.monotonic() - start) * 1000), 1)
        return ClipRenderResult(
            asset_url=str(manifest_path),
            duration_s=round(request.end_s - request.start_s, 2),
            provider="null",
            duration_ms=duration_ms,
        )
