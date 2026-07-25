"""Reference template-based renderer using Pillow + ffmpeg (via
imageio-ffmpeg's bundled binary). This is one *possible* implementation of
the VideoRenderer interface — not a dependency of the core system. Install
with `pip install '.[rendering]'` and set RENDERER_BACKEND=template_pillow
to use it; without the extra installed, selecting this backend raises a
clear error rather than the app failing to start.

Swapping this out for Remotion, Runway, Kling, or any other backend later
means writing a new VideoRenderer subclass and registering it in
factory.py — production_service.py and every other caller is unaffected,
which is the entire point of adjustment #2.
"""

import subprocess
import tempfile
import time
from pathlib import Path

from content_factory.video_production.renderer.base import RenderRequest, RenderResult, VideoRenderer

_FRAME_SIZE = (1080, 1920)  # 9:16 short-form


class TemplatePillowRenderer(VideoRenderer):
    def __init__(self, storage_dir: Path) -> None:
        self._storage_dir = storage_dir

    def render(self, request: RenderRequest) -> RenderResult:
        try:
            import imageio_ffmpeg
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:
            raise RuntimeError(
                "TemplatePillowRenderer requires the 'rendering' extra: "
                "pip install '.[rendering]'"
            ) from exc

        start = time.monotonic()
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        fps = 2  # captions update every ~0.5s is plenty for a text-overlay template
        duration_s = request.target_duration_s or (
            request.captions[-1].end_s if request.captions else 15.0
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            frame_paths = []
            total_frames = max(int(duration_s * fps), 1)
            for i in range(total_frames):
                t = i / fps
                cue = next(
                    (c for c in request.captions if c.start_s <= t < c.end_s),
                    None,
                )
                frame = Image.new("RGB", _FRAME_SIZE, color=(20, 20, 20))
                draw = ImageDraw.Draw(frame)
                text = cue.text if cue else request.hook_text
                try:
                    font = ImageFont.load_default(size=64)
                except TypeError:  # pragma: no cover - older Pillow
                    font = ImageFont.load_default()
                draw.text((80, _FRAME_SIZE[1] // 2), text, fill=(255, 255, 255), font=font)
                frame_path = Path(tmp_dir) / f"frame_{i:05d}.png"
                frame.save(frame_path)
                frame_paths.append(frame_path)

            asset_path = self._storage_dir / f"video_{request.video_id}.mp4"
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
            cmd = [
                ffmpeg_bin,
                "-y",
                "-framerate",
                str(fps),
                "-i",
                str(Path(tmp_dir) / "frame_%05d.png"),
            ]
            if request.voiceover_audio_path:
                cmd += ["-i", request.voiceover_audio_path]
            cmd += ["-pix_fmt", "yuv420p", str(asset_path)]
            subprocess.run(cmd, check=True, capture_output=True)

        duration_ms = int((time.monotonic() - start) * 1000)
        return RenderResult(
            asset_url=str(asset_path),
            duration_s=duration_s,
            provider="template_pillow",
            model="template-v1",
            model_version="v1",
            cost_usd=0.0,
            duration_ms=duration_ms,
        )
