from content_factory.config import Settings
from content_factory.logging_config import get_logger
from content_factory.video_production.tts.base import TTSProvider
from content_factory.video_production.tts.providers.silent_provider import SilentTTSProvider

logger = get_logger(__name__)


def get_tts_provider(settings: Settings) -> TTSProvider:
    provider = settings.resolved_tts_provider()
    storage_dir = settings.media_storage_path() / "audio"

    if provider == "elevenlabs":
        from content_factory.video_production.tts.providers.elevenlabs_provider import (
            ElevenLabsTTSProvider,
        )

        return ElevenLabsTTSProvider(api_key=settings.elevenlabs_api_key, storage_dir=storage_dir)

    if provider == "silent":
        if settings.tts_provider == "elevenlabs":
            logger.warning("tts_provider_fallback", reason="no_api_key", configured="elevenlabs")
        return SilentTTSProvider(storage_dir=storage_dir)

    raise ValueError(f"Unknown TTS provider: {provider!r}")
