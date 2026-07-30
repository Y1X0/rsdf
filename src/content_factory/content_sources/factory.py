"""Composition root for ContentSourceProvider — mirrors transcription/factory.py
exactly. api/routers/clips.py depends only on ContentSourceProvider,
obtained via this factory (injected through api/deps.py), never a concrete
provider class directly.
"""

from content_factory.config import Settings
from content_factory.content_sources.base import ContentSourceProvider
from content_factory.content_sources.providers.manual_provider import ManualContentSourceProvider
from content_factory.logging_config import get_logger

logger = get_logger(__name__)


def get_content_source_provider(settings: Settings) -> ContentSourceProvider:
    provider = settings.resolved_content_source_provider()

    if provider == "content_rewards":
        from content_factory.content_sources.providers.content_rewards_provider import ContentRewardsProvider

        return ContentRewardsProvider()

    if provider == "manual":
        return ManualContentSourceProvider()

    raise ValueError(f"Unknown content source provider: {provider!r}")
