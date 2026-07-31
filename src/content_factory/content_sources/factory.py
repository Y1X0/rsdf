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

        # Loud and impossible to miss on purpose: CONTENT_SOURCE_PROVIDER
        # only ever becomes "content_rewards" via an explicit env var an
        # operator has to set by hand (default is "manual"; render.yaml
        # does not set this) - but if that ever happens by mistake, this
        # warning fires on every single request that touches the sync
        # endpoint, making it immediately visible in structured logs. This
        # provider makes real external HTTP calls (contentrewards.com,
        # Google Drive) and has never been verified against the live site
        # by this session (Cloudflare blocks non-browser tools here) - see
        # docs/CONTENT_REWARDS_CONNECTOR.md for the real verification
        # workflow and this provider's known coverage gaps.
        logger.warning(
            "content_source_provider_is_content_rewards",
            detail=(
                "CONTENT_SOURCE_PROVIDER=content_rewards selects ContentRewardsProvider, "
                "which makes real external requests to contentrewards.com and the Google "
                "Drive API - see docs/CONTENT_REWARDS_CONNECTOR.md for its known "
                "limitations (only campaigns with a public Google Drive link in their "
                "description are covered; Cloudflare may block requests). If this is "
                "unexpected, unset CONTENT_SOURCE_PROVIDER (default 'manual' disables "
                "sourcing entirely)."
            ),
        )
        return ContentRewardsProvider(
            google_drive_api_key=settings.google_drive_api_key,
            max_video_bytes=settings.max_content_source_video_bytes,
        )

    if provider == "manual":
        return ManualContentSourceProvider()

    raise ValueError(f"Unknown content source provider: {provider!r}")
