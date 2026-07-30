"""Real Instagram Graph API provider (Reels container publish), targeting
**Instagram API with Instagram Login** — the standalone product where the
access token is issued directly to the Instagram account (no Facebook Page
required). `httpx` is only imported here, lazily (install with
`pip install '.[publishing]'`). Selected only when Instagram credentials
and a decrypted per-account access token are available (see
publishing/factory.py).

Real production bug this closes: this provider originally targeted the
*other* Instagram integration - classic "Facebook Login for Business"
(`graph.facebook.com`, a Facebook-Page-linked IG Business account). The
first real production account was set up via Instagram Login instead
(access tokens issued by that flow are prefixed `IGAA...`), and Meta
rejected every request against `graph.facebook.com` with
`{"error": {"message": "Invalid OAuth access token - Cannot parse access
token", "code": 190}}` - that token format simply isn't valid for that
host/product. Instagram API with Instagram Login uses its own host,
`graph.instagram.com`, for the exact same three-call flow.

Real Instagram video publishing is genuinely two Graph API calls, not one:
create a media container (`POST /{ig-user-id}/media`, returns a
`creation_id`), wait for Meta's servers to finish downloading/processing
the video (`GET /{creation_id}?fields=status_code` — asynchronous; a
freshly-created container is not immediately publishable), then
`POST /{ig-user-id}/media_publish` with that `creation_id`. `account_id`
is the real numeric Instagram User ID (`OwnedAccount.platform_account_id`)
obtained during the Instagram Login authorization flow — passed explicitly
rather than relying on "me" so the same code path works regardless of
which of Meta's two Instagram integrations a given token came from.
"""

import time

from content_factory.publishing.base import PublishingProvider, PublishRequest, PublishResult
from content_factory.retry import ProviderRequestRejected, RetryableProviderError, describe_http_error

_API_VERSION = "v21.0"
_MEDIA_URL_TEMPLATE = f"https://graph.instagram.com/{_API_VERSION}/{{account_id}}/media"
_MEDIA_PUBLISH_URL_TEMPLATE = f"https://graph.instagram.com/{_API_VERSION}/{{account_id}}/media_publish"
_CONTAINER_STATUS_URL_TEMPLATE = f"https://graph.instagram.com/{_API_VERSION}/{{creation_id}}"

# A short-form clip should finish processing well within this window; bounded
# so a stuck container can't hang the publish request forever. Tune upward
# if real usage shows longer videos need more time.
_POLL_INTERVAL_S = 5.0
_MAX_POLL_ATTEMPTS = 24  # ~2 minutes at the interval above


class InstagramContainerProcessingFailed(Exception):
    pass


class InstagramPublishingProvider(PublishingProvider):
    def __init__(self, access_token: str, account_id: str = "me") -> None:
        self._access_token = access_token
        self._account_id = account_id

    def publish(self, request: PublishRequest) -> PublishResult:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "InstagramPublishingProvider requires the 'publishing' extra: pip install '.[publishing]'"
            ) from exc

        caption = f"{request.title}\n{request.description}\n" + " ".join(
            f"#{tag}" for tag in request.hashtags
        )
        if request.contains_ai_voice or request.contains_ai_visual:
            caption = f"{caption}\n#ad #AIgenerated"

        creation_id = self._create_container(httpx, video_url=request.asset_url, caption=caption)
        self._wait_until_ready(httpx, creation_id=creation_id)
        media_id = self._publish_container(httpx, creation_id=creation_id)

        return PublishResult(provider="instagram", published=True, external_post_id=media_id)

    def _create_container(self, httpx, *, video_url: str, caption: str) -> str:
        try:
            response = httpx.post(
                _MEDIA_URL_TEMPLATE.format(account_id=self._account_id),
                params={"access_token": self._access_token},
                json={"video_url": video_url, "caption": caption, "media_type": "REELS"},
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("Instagram media container creation timed out") from exc

        if response.status_code >= 500:
            raise RetryableProviderError(
                f"Instagram media container creation returned {response.status_code}: {describe_http_error(response)}"
            )
        if response.status_code >= 400:
            raise ProviderRequestRejected(
                f"Instagram media container creation returned {response.status_code}: {describe_http_error(response)}"
            )
        return response.json()["id"]

    def _wait_until_ready(self, httpx, *, creation_id: str) -> None:
        for _ in range(_MAX_POLL_ATTEMPTS):
            try:
                response = httpx.get(
                    _CONTAINER_STATUS_URL_TEMPLATE.format(creation_id=creation_id),
                    params={"access_token": self._access_token, "fields": "status_code"},
                    timeout=30.0,
                )
            except httpx.TimeoutException as exc:
                raise RetryableProviderError("Instagram media container status check timed out") from exc

            if response.status_code >= 500:
                raise RetryableProviderError(
                    f"Instagram media container status check returned {response.status_code}: "
                    f"{describe_http_error(response)}"
                )
            if response.status_code >= 400:
                raise ProviderRequestRejected(
                    f"Instagram media container status check returned {response.status_code}: "
                    f"{describe_http_error(response)}"
                )

            status_code = response.json().get("status_code")
            if status_code == "FINISHED":
                return
            if status_code == "ERROR":
                raise InstagramContainerProcessingFailed(
                    f"Instagram media container {creation_id} failed processing (status_code=ERROR)"
                )
            time.sleep(_POLL_INTERVAL_S)

        raise RetryableProviderError(
            f"Instagram media container {creation_id} did not finish processing within "
            f"{_MAX_POLL_ATTEMPTS * _POLL_INTERVAL_S:.0f}s"
        )

    def _publish_container(self, httpx, *, creation_id: str) -> str | None:
        try:
            response = httpx.post(
                _MEDIA_PUBLISH_URL_TEMPLATE.format(account_id=self._account_id),
                params={"access_token": self._access_token},
                json={"creation_id": creation_id},
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("Instagram media_publish call timed out") from exc

        if response.status_code >= 500:
            raise RetryableProviderError(
                f"Instagram media_publish returned {response.status_code}: {describe_http_error(response)}"
            )
        if response.status_code >= 400:
            raise ProviderRequestRejected(
                f"Instagram media_publish returned {response.status_code}: {describe_http_error(response)}"
            )
        return response.json().get("id")
