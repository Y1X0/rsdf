"""Media backup (Production Hardening Sprint H3 — closing the production
readiness review's DR4 finding: rendered video/audio assets on local disk
have zero backup story independent of the database).

This is deliberately a **backup**, not a storage migration: local disk
(`MEDIA_STORAGE_DIR`) stays the primary read path everywhere in the
codebase (`Video.asset_url`, TTS `audio_path`, etc. are all untouched) —
this module only ever adds a best-effort copy of a file that already
exists locally to a second, durable location, on the same
"safe-default-provider" pattern as every other external integration in
this codebase (LLM, TTS, renderer, notifications, publishing, analytics
ingestion): a zero-dependency `NullMediaBackupProvider` default, and a
real `S3MediaBackupProvider` behind a lazy `boto3` import, selected only
when both the `storage` extra is installed and a bucket is configured.

Backup is explicitly best-effort and non-fatal: a failed backup logs a
warning and returns `None` rather than raising, because losing the backup
copy of a video that already rendered successfully should never be the
reason the render request itself fails.
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


class MediaBackupProvider(ABC):
    @abstractmethod
    def backup(self, local_path: str) -> MediaBackupResult:
        raise NotImplementedError


class NullMediaBackupProvider(MediaBackupProvider):
    """Default — no-op. Local disk remains the only copy, exactly Phase 1/2's
    existing behavior; selected whenever `MEDIA_BACKUP_ENABLED` is false or
    no backend is configured."""

    def backup(self, local_path: str) -> MediaBackupResult:
        return MediaBackupResult(backed_up=False, location=None)


class S3MediaBackupProvider(MediaBackupProvider):
    def __init__(self, *, bucket: str, prefix: str) -> None:
        self._bucket = bucket
        self._prefix = prefix.strip("/")

    def backup(self, local_path: str) -> MediaBackupResult:
        try:
            import boto3
        except ImportError:  # pragma: no cover - exercised only without the extra
            logger.warning("media_backup_skipped", reason="boto3_not_installed", local_path=local_path)
            return MediaBackupResult(backed_up=False, location=None)

        path = Path(local_path)
        if not path.is_file():
            logger.warning("media_backup_skipped", reason="local_file_missing", local_path=local_path)
            return MediaBackupResult(backed_up=False, location=None)

        key = f"{self._prefix}/{path.name}" if self._prefix else path.name
        try:
            boto3.client("s3").upload_file(str(path), self._bucket, key)
        except Exception:
            # Best-effort: a failed backup must never fail the render/TTS
            # request that already succeeded locally — log and move on.
            logger.error("media_backup_failed", local_path=local_path, bucket=self._bucket, key=key, exc_info=True)
            return MediaBackupResult(backed_up=False, location=None)

        location = f"s3://{self._bucket}/{key}"
        logger.info("media_backup_completed", local_path=local_path, location=location)
        return MediaBackupResult(backed_up=True, location=location)


def get_media_backup_provider(settings: Settings) -> MediaBackupProvider:
    if not settings.media_backup_enabled:
        return NullMediaBackupProvider()
    if not settings.media_backup_s3_bucket:
        logger.warning("media_backup_provider_fallback", reason="no_bucket_configured")
        return NullMediaBackupProvider()
    return S3MediaBackupProvider(bucket=settings.media_backup_s3_bucket, prefix=settings.media_backup_s3_prefix)
