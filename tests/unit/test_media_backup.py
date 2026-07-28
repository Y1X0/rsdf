"""Production Hardening Sprint H3 (DR4): media backup provider — a
best-effort copy of a locally-rendered asset to durable storage. Extended
to close the profit loop's #1 blocker: a successful upload can also
produce a real public https:// URL (media_backup_public_base_url), which
production_service.py/clip_service.py use to replace Video.asset_url, and
publishing_service.py refuses to publish without."""

from content_factory.config import Settings
from content_factory.services import media_backup
from content_factory.services.media_backup import (
    NullMediaBackupProvider,
    S3MediaBackupProvider,
    backup_and_get_public_url,
    get_media_backup_provider,
)


def test_null_provider_never_backs_up():
    result = NullMediaBackupProvider().backup("/tmp/whatever.mp4")
    assert result.backed_up is False
    assert result.location is None
    assert result.public_url is None


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
    assert result.public_url is None  # no public_base_url configured


def test_s3_provider_produces_a_public_url_when_base_url_configured(monkeypatch, tmp_path):
    """This is what actually closes the profit loop's storage blocker:
    without a public_url, production_service.py/clip_service.py have
    nothing to replace the local Video.asset_url with, and
    publishing_service.py refuses to publish."""
    local_file = tmp_path / "video_1.mp4"
    local_file.write_bytes(b"fake video bytes")

    class _FakeS3Client:
        def upload_file(self, filename, bucket, key):
            pass

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service, **kwargs: _FakeS3Client())

    provider = S3MediaBackupProvider(
        bucket="my-bucket",
        prefix="content-factory/media",
        public_base_url="https://pub-abc123.r2.dev/",
    )
    result = provider.backup(str(local_file))

    assert result.backed_up is True
    assert result.public_url == "https://pub-abc123.r2.dev/content-factory/media/video_1.mp4"


def test_s3_provider_uses_endpoint_url_for_r2_compatible_services(monkeypatch, tmp_path):
    local_file = tmp_path / "video_1.mp4"
    local_file.write_bytes(b"fake video bytes")

    captured_kwargs = {}

    class _FakeS3Client:
        def upload_file(self, filename, bucket, key):
            pass

    def _fake_client(service, **kwargs):
        captured_kwargs.update(kwargs)
        return _FakeS3Client()

    import boto3

    monkeypatch.setattr(boto3, "client", _fake_client)

    provider = S3MediaBackupProvider(
        bucket="my-bucket", prefix="media", endpoint_url="https://<account>.r2.cloudflarestorage.com"
    )
    provider.backup(str(local_file))

    assert captured_kwargs == {"endpoint_url": "https://<account>.r2.cloudflarestorage.com"}


def test_factory_passes_endpoint_url_and_public_base_url_through():
    settings = Settings(
        media_backup_enabled=True,
        media_backup_s3_bucket="my-bucket",
        media_backup_s3_endpoint_url="https://<account>.r2.cloudflarestorage.com",
        media_backup_public_base_url="https://pub-abc123.r2.dev",
    )
    provider = get_media_backup_provider(settings)

    assert isinstance(provider, S3MediaBackupProvider)
    assert provider._endpoint_url == "https://<account>.r2.cloudflarestorage.com"
    assert provider._public_base_url == "https://pub-abc123.r2.dev"


def test_backup_and_get_public_url_skips_remote_assets():
    import logging

    log = logging.getLogger("test")
    result = backup_and_get_public_url(NullMediaBackupProvider(), "https://already.example/video.mp4", log=log)
    assert result is None


def test_backup_and_get_public_url_skips_when_path_is_none():
    import logging

    log = logging.getLogger("test")
    result = backup_and_get_public_url(NullMediaBackupProvider(), None, log=log)
    assert result is None


def test_backup_and_get_public_url_returns_public_url_on_success(monkeypatch, tmp_path):
    import logging

    local_file = tmp_path / "video_1.mp4"
    local_file.write_bytes(b"fake video bytes")
    log = logging.getLogger("test")

    from content_factory.services.media_backup import MediaBackupProvider, MediaBackupResult

    class _FakeProvider(MediaBackupProvider):
        def backup(self, local_path: str) -> MediaBackupResult:
            return MediaBackupResult(backed_up=True, location="s3://x/y", public_url="https://cdn.example/y")

    result = backup_and_get_public_url(_FakeProvider(), str(local_file), log=log)
    assert result == "https://cdn.example/y"


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
