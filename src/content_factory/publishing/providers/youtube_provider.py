"""Real YouTube Data API v3 provider (Shorts upload). `httpx` is only
imported here, lazily (install with `pip install '.[publishing]'`).
Selected only when YouTube credentials and a decrypted per-account access
token are available (see publishing/factory.py) — never exercised against
the live API in this environment (ARCHITECTURE.md §13 flags YouTube's
quota limits), so this is unit-tested against mocked HTTP responses only.
Simplified to a metadata-only call against a pre-uploaded asset URL rather
than the real API's resumable multipart upload — update this when YouTube
access is actually provisioned and validated end-to-end.
"""

from content_factory.publishing.base import PublishingProvider, PublishRequest, PublishResult
from content_factory.retry import ProviderRequestRejected, RetryableProviderError, describe_http_error

_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


class YouTubePublishingProvider(PublishingProvider):
    def __init__(self, access_token: str) -> None:
        self._access_token = access_token

    def publish(self, request: PublishRequest) -> PublishResult:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "YouTubePublishingProvider requires the 'publishing' extra: pip install '.[publishing]'"
            ) from exc

        description = request.description
        if request.contains_ai_voice or request.contains_ai_visual:
            description = f"{description}\n\n[Contains AI-generated content]"

        try:
            response = httpx.post(
                _UPLOAD_URL,
                headers={"Authorization": f"Bearer {self._access_token}"},
                params={"part": "snippet,status"},
                json={
                    "snippet": {
                        "title": request.title,
                        "description": description,
                        "tags": request.hashtags,
                    },
                    "status": {"selfDeclaredMadeForKids": False, "privacyStatus": "public"},
                },
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("YouTube upload request timed out") from exc

        if response.status_code >= 500:
            raise RetryableProviderError(
                f"YouTube upload returned {response.status_code}: {describe_http_error(response)}"
            )
        if response.status_code >= 400:
            raise ProviderRequestRejected(
                f"YouTube upload returned {response.status_code}: {describe_http_error(response)}"
            )

        video_id = response.json().get("id")
        return PublishResult(provider="youtube", published=True, external_post_id=video_id)
