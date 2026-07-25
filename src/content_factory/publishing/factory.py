from content_factory.config import Settings
from content_factory.db.models.enums import AccountPlatform
from content_factory.logging_config import get_logger
from content_factory.publishing.base import PublishingProvider
from content_factory.publishing.providers.manual_provider import ManualPublishingProvider

logger = get_logger(__name__)

_PLATFORM_CREDENTIAL_CHECK = {
    AccountPlatform.TIKTOK: lambda s: bool(s.tiktok_client_key),
    AccountPlatform.YOUTUBE: lambda s: bool(s.youtube_client_id),
    AccountPlatform.INSTAGRAM: lambda s: bool(s.instagram_app_id),
}


def get_publishing_provider(
    platform: AccountPlatform, settings: Settings, access_token: str | None = None
) -> PublishingProvider:
    """Falls back to ManualPublishingProvider whenever platform-level
    credentials aren't configured *or* the specific account has no
    decrypted access token yet — matching every other provider factory's
    "no secret -> safe zero-dependency default" rule."""
    has_platform_credentials = _PLATFORM_CREDENTIAL_CHECK.get(platform, lambda _: False)(settings)

    if not (has_platform_credentials and access_token):
        if has_platform_credentials and not access_token:
            logger.warning("publishing_provider_fallback", reason="no_account_access_token", platform=platform.value)
        return ManualPublishingProvider()

    if platform == AccountPlatform.TIKTOK:
        from content_factory.publishing.providers.tiktok_provider import TikTokPublishingProvider

        return TikTokPublishingProvider(access_token=access_token)

    if platform == AccountPlatform.YOUTUBE:
        from content_factory.publishing.providers.youtube_provider import YouTubePublishingProvider

        return YouTubePublishingProvider(access_token=access_token)

    if platform == AccountPlatform.INSTAGRAM:
        from content_factory.publishing.providers.instagram_provider import InstagramPublishingProvider

        return InstagramPublishingProvider(access_token=access_token)

    return ManualPublishingProvider()  # pragma: no cover - AccountPlatform is exhaustive above
