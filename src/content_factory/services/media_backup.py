"""Media backup and public asset hosting (originally Production Hardening
Sprint H3 / DR4; extended to close the profit loop's #1 blocker: no
platform can publish an asset it can't reach).

Local disk (`MEDIA_STORAGE_DIR`) is transient scratch space, not a
durable or reachable location: on single-instance free/starter hosting
(Render) it can be wiped on every restart, and even when it survives, no
external platform (TikTok/Instagram/YouTube) can ever reach a local
filesystem path to pull the video for publishing. This module's upload is
therefore no longer just a best-effort DR copy — when
`media_backup_public_base_url` is configured, a successful upload
produces a real public HTTPS URL that callers (production_service.py,
clip_service.py) use to *replace* `Video.asset_url`, which is what
publishing_service.py actually hands to every platform provider.

Same "safe-default-provider" pattern as every other external integration
in this codebase: a zero-dependency `NullMediaBackupProvider` default,
and a real `S3MediaBackupProvider` (works with AWS S3 or any S3-compatible
service, e.g. Cloudflare R2, via `media_backup_s3_endpoint_url`) behind a
lazy `boto3` import, selected only when the `storage` extra is installed
and a bucket is configured.

Upload is still best-effort/non-fatal at the render step itself (a failed
upload logs a warning and returns no public_url rather than raising,
because losing the durable copy of a video that already rendered
successfully should never be the reason the render request itself fails)
— but publishing_service.py refuses to publish an asset that never got a
public URL, so a failed upload is never silently sent to a platform as a
broken local path; it fails loud, at the one place it actually matters.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from content_factory.config import Settings
from content_factory.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class MediaBackupResult:
    backed_up: bool
    location: str | None = None
    # A real, platform-fetchable https:// URL — only set when
    # `media_backup_public_base_url` is configured. `location` above may
    # still be an `s3://` URI even when `public_url` is None (uploaded,
    # but not reachable by anything outside this AWS account/bucket).
    public_url: str | None = None


class MediaBackupProvider(ABC):
    @abstractmethod
    def backup(self, local_path: str) -> MediaBackupResult:
        raise NotImplementedError


class NullMediaBackupProvider(MediaBackupProvider):
    """Default — no-op. Local disk remains the only copy, exactly Phase 1/2's
    existing behavior; selected whenever `MEDIA_BACKUP_ENABLED` is false or
    no backend is configured."""

    def backup(self, local_path: str) -> MediaBackupResult:
        return MediaBackupResult(backed_up=False, location=None, public_url=None)


class S3MediaBackupProvider(MediaBackupProvider):
    """Works against real AWS S3 *or* any S3-compatible object store (e.g.
    Cloudflare R2) — `endpoint_url` is the only thing that differs; boto3's
    client accepts an S3-compatible endpoint unchanged. `public_base_url`
    is deliberately explicit configuration rather than something derived
    from the bucket name: AWS's default virtual-hosted URL only works for
    a bucket with public-read enabled, and R2 requires either its own
    "r2.dev" public-access URL or a custom domain — there is no single
    formula that's correct for both, so the operator states the real,
    already-verified-reachable base URL directly."""

    def __init__(self, *, bucket: str, prefix: str, endpoint_url: str = "", public_base_url: str = "") -> None:
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._endpoint_url = endpoint_url or None
        self._public_base_url = public_base_url.rstrip("/")

    def backup(self, local_path: str) -> MediaBackupResult:
        try:
            import boto3
        except ImportError:  # pragma: no cover - exercised only without the extra
            logger.warning("media_backup_skipped", reason="boto3_not_installed", local_path=local_path)
            return MediaBackupResult(backed_up=False, location=None, public_url=None)

        path = Path(local_path)
        if not path.is_file():
            logger.warning("media_backup_skipped", reason="local_file_missing", local_path=local_path)
            return MediaBackupResult(backed_up=False, location=None, public_url=None)

        key = f"{self._prefix}/{path.name}" if self._prefix else path.name
        try:
            client = boto3.client("s3", endpoint_url=self._endpoint_url) if self._endpoint_url else boto3.client("s3")
            client.upload_file(str(path), self._bucket, key)
        except Exception:
            # Best-effort: a failed upload must never fail the render
            # request that already succeeded locally — log and move on.
            # publishing_service.py is what actually enforces that a
            # video without a public_url never reaches a platform.
            logger.error("media_backup_failed", local_path=local_path, bucket=self._bucket, key=key, exc_info=True)
            return MediaBackupResult(backed_up=False, location=None, public_url=None)

        location = f"s3://{self._bucket}/{key}"
        public_url = f"{self._public_base_url}/{key}" if self._public_base_url else None
        logger.info("media_backup_completed", local_path=local_path, location=location, public_url=public_url)
        return MediaBackupResult(backed_up=True, location=location, public_url=public_url)


def backup_and_get_public_url(provider: MediaBackupProvider, local_path: str | None, *, log) -> str | None:
    """Shared by production_service.py and clip_service.py: best-effort,
    never fatal — a failed or skipped backup must never fail an otherwise-
    successful render. Skips already-remote assets (nothing local to
    copy). Returns a public URL only when the upload succeeded and one was
    produced (media_backup_public_base_url configured) — callers use this
    to replace the local path on the Video row, since publishing_service.py
    reads that field directly and a platform can never reach a local
    filesystem path."""
    if not local_path or local_path.startswith(("http://", "https://")):
        return None
    try:
        result = provider.backup(local_path)
        if result.backed_up:
            log.info("media_backed_up", local_path=local_path, location=result.location, public_url=result.public_url)
        return result.public_url
    except Exception:
        log.error("media_backup_unexpected_error", local_path=local_path, exc_info=True)
        return None


def get_media_backup_provider(settings: Settings) -> MediaBackupProvider:
    if not settings.media_backup_enabled:
        return NullMediaBackupProvider()
    if not settings.media_backup_s3_bucket:
        logger.warning("media_backup_provider_fallback", reason="no_bucket_configured")
        return NullMediaBackupProvider()
    return S3MediaBackupProvider(
        bucket=settings.media_backup_s3_bucket,
        prefix=settings.media_backup_s3_prefix,
        endpoint_url=settings.media_backup_s3_endpoint_url,
        public_base_url=settings.media_backup_public_base_url,
    )
