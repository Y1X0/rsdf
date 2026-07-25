"""Production Hardening Sprint H3 (DR4): media backup provider — a
best-effort copy of a locally-rendered asset to durable storage, never a
storage migration (local disk stays the primary read path everywhere
else in the codebase)."""

from content_factory.config import Settings
from content_factory.services import media_backup
from content_factory.services.media_backup import (
    NullMediaBackupProvider,
    S3MediaBackupProvider,
    get_media_backup_provider,
)


def test_null_provider_never_backs_up():
    result = NullMediaBackupProvider().backup("/tmp/whatever.mp4")
    assert result.backed_up is False
    assert result.location is None


def test_factory_returns_null_provider_when_disabled():
    settings = Settings(media_backup_enabled=False, media_backup_s3_bucket="some-bucket")
    provider = get_media_backup_provider(settings)
    assert isinstance(provider, NullMediaBackupProvider)


def test_factory_falls_back_to_null_when_enabled_but_no_bucket():
    settings = Settings(media_backup_enabled=True, media_backup_s3_bucket="")
    provider = get_media_backup_provider(settings)
    assert isinstance(provider, NullMediaBackupProvider)


def test_factory_returns_s3_provider_when_fully_configured():
    settings = Settings(media_backup_enabled=True, media_backup_s3_bucket="my-bucket", media_backup_s3_prefix="x")
    provider = get_media_backup_provider(settings)
    assert isinstance(provider, S3MediaBackupProvider)


def test_s3_provider_skips_missing_local_file():
    provider = S3MediaBackupProvider(bucket="my-bucket", prefix="media")
    result = provider.backup("/tmp/does-not-exist-at-all.mp4")
    assert result.backed_up is False


def test_s3_provider_uploads_existing_file(monkeypatch, tmp_path):
    local_file = tmp_path / "video_1.mp4"
    local_file.write_bytes(b"fake video bytes")

    uploads = []

    class _FakeS3Client:
        def upload_file(self, filename, bucket, key):
            uploads.append((filename, bucket, key))

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service: _FakeS3Client())

    provider = S3MediaBackupProvider(bucket="my-bucket", prefix="content-factory/media")
    result = provider.backup(str(local_file))

    assert result.backed_up is True
    assert result.location == "s3://my-bucket/content-factory/media/video_1.mp4"
    assert uploads == [(str(local_file), "my-bucket", "content-factory/media/video_1.mp4")]


def test_s3_provider_upload_failure_is_non_fatal(monkeypatch, tmp_path):
    local_file = tmp_path / "video_2.mp4"
    local_file.write_bytes(b"fake video bytes")

    class _FailingS3Client:
        def upload_file(self, filename, bucket, key):
            raise RuntimeError("network error")

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service: _FailingS3Client())

    provider = S3MediaBackupProvider(bucket="my-bucket", prefix="media")
    result = provider.backup(str(local_file))  # must not raise
    assert result.backed_up is False


def test_s3_provider_falls_back_gracefully_without_boto3(monkeypatch, tmp_path):
    local_file = tmp_path / "video_3.mp4"
    local_file.write_bytes(b"fake video bytes")

    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "boto3":
            raise ImportError("no module named boto3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    provider = S3MediaBackupProvider(bucket="my-bucket", prefix="media")
    result = provider.backup(str(local_file))  # must not raise
    assert result.backed_up is False


def test_no_prefix_uses_bare_filename(monkeypatch, tmp_path):
    local_file = tmp_path / "clip.mp4"
    local_file.write_bytes(b"x")

    captured = {}

    class _FakeS3Client:
        def upload_file(self, filename, bucket, key):
            captured["key"] = key

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service: _FakeS3Client())

    provider = S3MediaBackupProvider(bucket="my-bucket", prefix="")
    provider.backup(str(local_file))
    assert captured["key"] == "clip.mp4"


def test_module_import_is_clean():
    # media_backup module itself must not require boto3 at import time.
    assert hasattr(media_backup, "get_media_backup_provider")
