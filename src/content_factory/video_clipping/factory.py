from content_factory.config import Settings
from content_factory.video_clipping.base import ClipRenderer
from content_factory.video_clipping.providers.null_clip_renderer import NullClipRenderer


def get_clip_renderer(settings: Settings) -> ClipRenderer:
    backend = settings.clip_renderer_backend
    storage_dir = settings.media_storage_path() / "clips"

    if backend == "null":
        return NullClipRenderer(storage_dir=storage_dir)

    if backend == "ffmpeg":
        from content_factory.video_clipping.providers.ffmpeg_clip_renderer import FfmpegClipRenderer

        return FfmpegClipRenderer(storage_dir=storage_dir)

    raise ValueError(f"Unknown clip renderer backend: {backend!r}")
