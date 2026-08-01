"""services/media_availability.py — ensure_local_media_available() is
tested directly against a fake ContentSourceProvider (same
mocked-provider philosophy as test_content_sources.py), never a real
network call."""

import os

import pytest

from content_factory.content_sources.base import RemoteCampaignVideo
from content_factory.db.models.enums import SourceVideoOrigin
from content_factory.db.models.source_video import SourceVideo
from content_factory.services.media_availability import MediaUnavailableError, ensure_local_media_available


class _FakeContentSourceProvider:
    def __init__(self, videos: list[RemoteCampaignVideo] | None = None):
        self.videos = videos or []
        self.download_calls: list[str] = []

    def list_available_videos(self) -> list[RemoteCampaignVideo]:
        return self.videos

    def download_video(self, video: RemoteCampaignVideo, destination_path: str) -> None:
        self.download_calls.append(video.external_id)
        with open(destination_path, "wb") as f:
            f.write(b"recovered bytes")


def _content_rewards_video(db_session, *, storage_path: str, external_source_id: str = "ext-1") -> SourceVideo:
    sv = SourceVideo(
        title="Fake Video", source=SourceVideoOrigin.CONTENT_REWARDS,
        external_source_id=external_source_id, storage_path=storage_path,
    )
    db_session.add(sv)
    db_session.commit()
    return sv


def test_ensure_local_media_available_is_a_noop_when_the_file_already_exists(db_session, tmp_path):
    real_file = tmp_path / "existing.mp4"
    real_file.write_bytes(b"already here")
    sv = _content_rewards_video(db_session, storage_path=str(real_file))
    provider = _FakeContentSourceProvider()

    ensure_local_media_available(
        db_session, source_video=sv, content_source_provider=provider, storage_dir=tmp_path
    )

    assert provider.download_calls == []
    assert real_file.read_bytes() == b"already here"


def test_ensure_local_media_available_recovers_a_missing_content_rewards_file(db_session, tmp_path):
    """Real production incident: a service with no persistent Disk lost a
    SourceVideo's file to a routine spin-down/restart, well after the
    original sync completed - a later transcribe attempt must recover the
    exact same row instead of crashing with a raw FileNotFoundError."""
    vanished_path = str(tmp_path / "vanished.mp4")  # never created - simulates the wipe
    sv = _content_rewards_video(db_session, storage_path=vanished_path, external_source_id="ext-1")
    original_id = sv.id
    provider = _FakeContentSourceProvider(
        videos=[
            RemoteCampaignVideo(
                external_id="ext-1", title="t", campaign_name="c", duration_s=5.0,
                download_url="", source_page_url="https://example.test/1",
            )
        ]
    )

    ensure_local_media_available(
        db_session, source_video=sv, content_source_provider=provider, storage_dir=tmp_path
    )

    assert provider.download_calls == ["ext-1"]
    assert sv.id == original_id  # same row, never a new SourceVideo
    assert sv.storage_path != vanished_path
    assert os.path.exists(sv.storage_path)


def test_ensure_local_media_available_raises_a_clear_error_when_the_source_is_not_recoverable(db_session, tmp_path):
    """A manually-uploaded video (source=UPLOAD) has no external source to
    re-fetch from - this must surface as a clear, specific error, not a
    raw FileNotFoundError bubbling out of the transcription provider."""
    vanished_path = str(tmp_path / "vanished.mp4")
    sv = SourceVideo(title="Manually uploaded", source=SourceVideoOrigin.UPLOAD, storage_path=vanished_path)
    db_session.add(sv)
    db_session.commit()
    provider = _FakeContentSourceProvider()

    with pytest.raises(MediaUnavailableError, match="cannot be automatically recovered"):
        ensure_local_media_available(
            db_session, source_video=sv, content_source_provider=provider, storage_dir=tmp_path
        )
    assert provider.download_calls == []


def test_ensure_local_media_available_raises_a_clear_error_when_the_remote_campaign_is_gone(db_session, tmp_path):
    """The video's source is CONTENT_REWARDS, but the campaign is no longer
    listed by the provider (e.g. it ended) - still a clear error, not a
    silent no-op or a raw crash."""
    vanished_path = str(tmp_path / "vanished.mp4")
    sv = _content_rewards_video(db_session, storage_path=vanished_path, external_source_id="ext-gone")
    provider = _FakeContentSourceProvider(videos=[])  # campaign no longer listed

    with pytest.raises(MediaUnavailableError, match="no longer listed"):
        ensure_local_media_available(
            db_session, source_video=sv, content_source_provider=provider, storage_dir=tmp_path
        )
