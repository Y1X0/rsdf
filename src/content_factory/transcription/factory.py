"""Composition root for TranscriptionProvider — mirrors llm/factory.py
exactly. services/clip_service.py depends only on TranscriptionProvider,
obtained via this factory (injected through api/deps.py), never a concrete
provider class directly.
"""

from content_factory.config import Settings
from content_factory.logging_config import get_logger
from content_factory.transcription.base import TranscriptionProvider
from content_factory.transcription.providers.null_provider import NullTranscriptionProvider

logger = get_logger(__name__)


def get_transcription_provider(settings: Settings) -> TranscriptionProvider:
    provider = settings.resolved_transcription_provider()

    if provider == "groq":
        from content_factory.transcription.providers.groq_whisper_provider import GroqWhisperProvider

        return GroqWhisperProvider(api_key=settings.groq_api_key, model=settings.groq_whisper_model)

    if provider == "null":
        if settings.transcription_provider == "groq":
            logger.warning(
                "transcription_provider_fallback",
                reason="no_api_key",
                configured_provider=settings.transcription_provider,
            )
        return NullTranscriptionProvider()

    raise ValueError(f"Unknown transcription provider: {provider!r}")
