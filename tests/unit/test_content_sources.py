"""content_sources/ — Milestone 1 of the Content Rewards Connector: the
interface, factory selection, and the placeholder ContentRewardsProvider
used until real Content Rewards API requests are captured (from the
browser's DevTools) and wired into a real httpx-based provider."""

import pytest

from content_factory.config import Settings
from content_factory.content_sources.base import RemoteCampaignVideo
from content_factory.content_sources.factory import get_content_source_provider
from content_factory.content_sources.providers.content_rewards_provider import ContentRewardsProvider
from content_factory.content_sources.providers.manual_provider import ManualContentSourceProvider


def _video(external_id: str = "x") -> RemoteCampaignVideo:
    return RemoteCampaignVideo(
        external_id=external_id, title="t", campaign_name="c", duration_s=None,
        download_url="", source_page_url="",
    )


def test_manual_provider_returns_no_videos():
    assert ManualContentSourceProvider().list_available_videos() == []


def test_manual_provider_raises_on_download():
    with pytest.raises(NotImplementedError):
        ManualContentSourceProvider().download_video(_video(), "/tmp/whatever.mp4")


def test_factory_returns_manual_provider_by_default():
    settings = Settings(content_source_provider="manual")
    assert isinstance(get_content_source_provider(settings), ManualContentSourceProvider)


def test_factory_returns_content_rewards_provider_when_configured():
    settings = Settings(content_source_provider="content_rewards")
    assert isinstance(get_content_source_provider(settings), ContentRewardsProvider)


def test_factory_raises_on_unknown_provider():
    settings = Settings(content_source_provider="something_else")
    with pytest.raises(ValueError):
        get_content_source_provider(settings)


def test_content_rewards_provider_lists_placeholder_videos():
    videos = ContentRewardsProvider().list_available_videos()
    assert len(videos) >= 1
    assert all(isinstance(v, RemoteCampaignVideo) for v in videos)
    assert all(v.external_id for v in videos)
    # Two calls must return the same catalog with the same external_ids -
    # the sync endpoint's idempotency depends on external_id being stable.
    assert [v.external_id for v in videos] == [v.external_id for v in ContentRewardsProvider().list_available_videos()]


def test_content_rewards_provider_download_produces_a_real_file(tmp_path):
    """Real production philosophy this codebase already applies everywhere
    else (verify_production_pipeline.sh, test_clip_factory_full_pipeline.py):
    even a placeholder should produce a genuinely real, non-empty file
    rather than a hollow stand-in, so downstream code (transcription,
    rendering) has something real to work with."""
    provider = ContentRewardsProvider()
    video = provider.list_available_videos()[0]
    dest = tmp_path / "out.mp4"

    provider.download_video(video, str(dest))

    assert dest.exists()
    assert dest.stat().st_size > 0
