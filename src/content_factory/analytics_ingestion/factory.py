from content_factory.analytics_ingestion.base import PlatformAnalyticsProvider
from content_factory.analytics_ingestion.providers.manual_provider import ManualAnalyticsProvider
from content_factory.config import Settings
from content_factory.db.models.enums import AccountPlatform
from content_factory.logging_config import get_logger

logger = get_logger(__name__)

# Same credential surface as publishing/factory.py — a platform's client
# credentials plus a per-account decrypted access token, both required
# before a real provider is selected.
_PLATFORM_CREDENTIAL_CHECK = {
    AccountPlatform.TIKTOK: lambda s: bool(s.tiktok_client_key),
    AccountPlatform.YOUTUBE: lambda s: bool(s.youtube_client_id),
    AccountPlatform.INSTAGRAM: lambda s: bool(s.instagram_app_id),
}


def get_analytics_provider(
    platform: AccountPlatform, settings: Settings, access_token: str | None = None
) -> PlatformAnalyticsProvider:
    has_platform_credentials = _PLATFORM_CREDENTIAL_CHECK.get(platform, lambda _: False)(settings)

    if not (has_platform_credentials and access_token):
        if has_platform_credentials and not access_token:
            logger.warning("analytics_provider_fallback", reason="no_account_access_token", platform=platform.value)
        return ManualAnalyticsProvider()

    if platform == AccountPlatform.TIKTOK:
        from content_factory.analytics_ingestion.providers.tiktok_provider import TikTokAnalyticsProvider

        return TikTokAnalyticsProvider(access_token=access_token)

    if platform == AccountPlatform.YOUTUBE:
        from content_factory.analytics_ingestion.providers.youtube_provider import YouTubeAnalyticsProvider

        return YouTubeAnalyticsProvider(access_token=access_token)

    if platform == AccountPlatform.INSTAGRAM:
        from content_factory.analytics_ingestion.providers.instagram_provider import InstagramAnalyticsProvider

        return InstagramAnalyticsProvider(access_token=access_token)

    return ManualAnalyticsProvider()  # pragma: no cover - AccountPlatform is exhaustive above
