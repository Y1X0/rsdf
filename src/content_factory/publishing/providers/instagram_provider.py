"""Real Instagram Graph API provider (Reels container publish). `httpx` is
only imported here, lazily (install with `pip install '.[publishing]'`).
Selected only when Instagram credentials and a decrypted per-account access
token are available (see publishing/factory.py) — never exercised against
the live API in this environment (ARCHITECTURE.md §13 flags Instagram's
Meta App Review requirement), so this is unit-tested against mocked HTTP
responses only. Simplified to a single container-create-and-publish call
rather than the real API's separate create/poll/publish steps — update
this when Instagram access is actually provisioned and validated
end-to-end.
"""

from content_factory.publishing.base import PublishingProvider, PublishRequest, PublishResult
from content_factory.publishing.retry import RetryableProviderError

_PUBLISH_URL_TEMPLATE = "https://graph.facebook.com/v19.0/{account_id}/media_publish"


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

        try:
            response = httpx.post(
                _PUBLISH_URL_TEMPLATE.format(account_id=self._account_id),
                params={"access_token": self._access_token},
                json={"video_url": request.asset_url, "caption": caption, "media_type": "REELS"},
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("Instagram publish request timed out") from exc

        if response.status_code >= 500:
            raise RetryableProviderError(f"Instagram publish returned {response.status_code}")
        response.raise_for_status()

        media_id = response.json().get("id")
        return PublishResult(provider="instagram", published=True, external_post_id=media_id)
