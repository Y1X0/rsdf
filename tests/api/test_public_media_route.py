"""The zero-cost alternative to an S3/R2 account for publishing (see
services/media_backup.py::LocalDiskMediaBackupProvider): api/main.py mounts
`/public-media` as an unauthenticated static route serving
settings.media_storage_dir directly, so an external platform can fetch a
rendered asset without this app needing any cloud storage account.

The mount's directory is captured once at app-creation time (module import),
unlike per-request settings, so this writes into the real, already-resolved
media_storage_path() rather than monkeypatching MEDIA_STORAGE_DIR."""

from content_factory.config import get_settings


def test_public_media_route_serves_a_file_without_auth(client):
    storage_dir = get_settings().media_storage_path()
    probe_path = storage_dir / "public_media_route_probe.txt"
    probe_path.write_bytes(b"hello from a rendered clip")
    try:
        unauthenticated_response = client.get(
            "/public-media/public_media_route_probe.txt", headers={"Authorization": ""}
        )
        assert unauthenticated_response.status_code == 200
        assert unauthenticated_response.content == b"hello from a rendered clip"
    finally:
        probe_path.unlink(missing_ok=True)


def test_public_media_route_serves_files_in_subdirectories(client):
    storage_dir = get_settings().media_storage_path()
    sub_dir = storage_dir / "clips"
    sub_dir.mkdir(parents=True, exist_ok=True)
    probe_path = sub_dir / "public_media_route_probe_clip.mp4"
    probe_path.write_bytes(b"fake mp4 bytes")
    try:
        resp = client.get("/public-media/clips/public_media_route_probe_clip.mp4", headers={"Authorization": ""})
        assert resp.status_code == 200
        assert resp.content == b"fake mp4 bytes"
    finally:
        probe_path.unlink(missing_ok=True)


def test_public_media_route_404s_for_missing_file(client):
    resp = client.get("/public-media/definitely-does-not-exist.mp4", headers={"Authorization": ""})
    assert resp.status_code == 404
