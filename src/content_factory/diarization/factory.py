"""Composition root for SpeakerDiarizationProvider - mirrors
transcription/factory.py exactly. services/clip_service.py depends only on
SpeakerDiarizationProvider, obtained via this factory (injected through
api/deps.py), never a concrete provider class directly.
"""

from content_factory.config import Settings
from content_factory.diarization.base import SpeakerDiarizationProvider
from content_factory.diarization.providers.null_provider import NullDiarizationProvider
from content_factory.logging_config import get_logger

logger = get_logger(__name__)


def get_diarization_provider(settings: Settings) -> SpeakerDiarizationProvider:
    provider = settings.resolved_diarization_provider()

    if provider == "pyannote":
        from content_factory.diarization.providers.pyannote_provider import PyannoteDiarizationProvider

        return PyannoteDiarizationProvider(hf_token=settings.huggingface_token)

    if provider == "null":
        if settings.diarization_provider == "pyannote":
            logger.warning(
                "diarization_provider_fallback",
                reason="no_huggingface_token",
                configured_provider=settings.diarization_provider,
            )
        return NullDiarizationProvider()

    raise ValueError(f"Unknown diarization provider: {provider!r}")
