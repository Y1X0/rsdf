from content_factory.config import Settings
from content_factory.video_production.renderer.base import VideoRenderer
from content_factory.video_production.renderer.providers.null_renderer import NullRenderer


def get_video_renderer(settings: Settings) -> VideoRenderer:
    backend = settings.renderer_backend
    storage_dir = settings.media_storage_path() / "video"

    if backend == "null":
        return NullRenderer(storage_dir=storage_dir)

    if backend == "template_pillow":
        from content_factory.video_production.renderer.providers.template_pillow import (
            TemplatePillowRenderer,
        )

        return TemplatePillowRenderer(storage_dir=storage_dir)

    raise ValueError(f"Unknown renderer backend: {backend!r}")
