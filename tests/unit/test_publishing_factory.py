"""publishing/factory.py: platform/credential-gated provider selection,
and that the real per-account platform_account_id actually reaches
InstagramPublishingProvider - without it, real Instagram publishing
addresses "me" instead of the real IG Business Account ID and fails."""

from content_factory.config import Settings
from content_factory.db.models.enums import AccountPlatform
from content_factory.publishing.factory import get_publishing_provider
from content_factory.publishing.providers.instagram_provider import InstagramPublishingProvider
from content_factory.publishing.providers.manual_provider import ManualPublishingProvider


def test_falls_back_to_manual_when_no_platform_credentials_configured():
    settings = Settings()
    provider = get_publishing_provider(AccountPlatform.INSTAGRAM, settings, access_token="token")
    assert isinstance(provider, ManualPublishingProvider)


def test_falls_back_to_manual_when_platform_configured_but_no_account_token():
    settings = Settings(instagram_app_id="app-123")
    provider = get_publishing_provider(AccountPlatform.INSTAGRAM, settings, access_token=None)
    assert isinstance(provider, ManualPublishingProvider)


def test_returns_real_instagram_provider_with_account_id_when_fully_configured():
    settings = Settings(instagram_app_id="app-123")
    provider = get_publishing_provider(
        AccountPlatform.INSTAGRAM, settings, access_token="real-token", account_id="17841440632369231"
    )
    assert isinstance(provider, InstagramPublishingProvider)
    assert provider._account_id == "17841440632369231"
    assert provider._access_token == "real-token"


def test_instagram_provider_defaults_to_me_when_account_id_not_supplied():
    """Not every registered account needs platform_account_id right away
    (e.g. before any real OAuth connection exists) - the provider must
    still construct, just with the pre-existing "me" default."""
    settings = Settings(instagram_app_id="app-123")
    provider = get_publishing_provider(AccountPlatform.INSTAGRAM, settings, access_token="real-token")
    assert isinstance(provider, InstagramPublishingProvider)
    assert provider._account_id == "me"
