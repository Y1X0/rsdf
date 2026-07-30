"""Real TikTok Content Posting API provider. `httpx` is only imported here,
lazily — the core install has no hard dependency on it (install with
`pip install '.[publishing]'`). Selected only when TikTok credentials and a
decrypted per-account access token are available (see publishing/factory.py)
— never exercised against the live API in this environment (ARCHITECTURE.md
§0/§13 flag TikTok's app-review timeline as an open, unresolved item), so
this is unit-tested against mocked HTTP responses only. Simplified to a
single "init and publish by URL" call rather than the full
init-upload-poll flow the real API supports — update this when TikTok
access is actually provisioned and validated end-to-end.
"""

from content_factory.publishing.base import PublishingProvider, PublishRequest, PublishResult
from content_factory.retry import ProviderRequestRejected, RetryableProviderError, describe_http_error

_PUBLISH_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"


class TikTokPublishingProvider(PublishingProvider):
    def __init__(self, access_token: str) -> None:
        self._access_token = access_token

    def publish(self, request: PublishRequest) -> PublishResult:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "TikTokPublishingProvider requires the 'publishing' extra: pip install '.[publishing]'"
            ) from exc

        caption = f"{request.title}\n{request.description}\n" + " ".join(
            f"#{tag}" for tag in request.hashtags
        )
        try:
            response = httpx.post(
                _PUBLISH_URL,
                headers={"Authorization": f"Bearer {self._access_token}"},
                json={
                    "post_info": {
                        "title": caption,
                        "disclose_ai_generated": request.contains_ai_voice or request.contains_ai_visual,
                    },
                    "source_info": {"source": "PULL_FROM_URL", "video_url": request.asset_url},
                },
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("TikTok publish request timed out") from exc

        if response.status_code >= 500:
            raise RetryableProviderError(
                f"TikTok publish returned {response.status_code}: {describe_http_error(response)}"
            )
        if response.status_code >= 400:
            raise ProviderRequestRejected(
                f"TikTok publish returned {response.status_code}: {describe_http_error(response)}"
            )

        publish_id = response.json().get("data", {}).get("publish_id")
        return PublishResult(provider="tiktok", published=True, external_post_id=publish_id)
