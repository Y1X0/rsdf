"""Content Rewards connector — Milestone 1 (see docs/CONTENT_REWARDS_CONNECTOR.md).

**This is deliberately a placeholder, not the real integration yet.**
Content Rewards (contentrewards.com) has no confirmed public,
creator-facing API for campaign/video data — the chosen path forward
(confirmed with the project owner) is a plain `httpx` provider built
against the site's own internal requests, captured from the browser's
DevTools Network tab while logged in. Until those exact requests are
captured and handed over, `ContentRewardsProvider` returns clearly-labeled
synthetic campaign videos (and generates a real, tiny, genuinely playable
video file for each on download) so the rest of the connector — the DB
columns, the idempotent sync endpoint, and the fact that a fetched video
flows through the *unmodified* transcribe/analyze/render/review/publish
pipeline exactly like a manual upload does — can be built and verified
end-to-end today, without waiting on real credentials.

Replacing the body of `list_available_videos`/`download_video` with real
`httpx` calls against the captured requests is the only change Milestone 2
needs — `content_sources/base.py`'s interface, `factory.py`'s selection
logic, and every caller stay exactly as they are.
"""

import subprocess

from content_factory.content_sources.base import ContentSourceProvider, RemoteCampaignVideo
from content_factory.logging_config import get_logger

logger = get_logger(__name__)

# Synthetic placeholder catalog — not real Content Rewards data. Two
# entries (not one) so sync-endpoint tests can exercise "more than one new
# video in a single sync" without any real network access.
_PLACEHOLDER_VIDEOS = [
    RemoteCampaignVideo(
        external_id="placeholder-campaign-1",
        title="[PLACEHOLDER] Content Rewards sync — sample campaign 1",
        campaign_name="placeholder-campaign",
        duration_s=6.0,
        download_url="",
        source_page_url="https://contentrewards.com/discover",
    ),
    RemoteCampaignVideo(
        external_id="placeholder-campaign-2",
        title="[PLACEHOLDER] Content Rewards sync — sample campaign 2",
        campaign_name="placeholder-campaign",
        duration_s=6.0,
        download_url="",
        source_page_url="https://contentrewards.com/discover",
    ),
]


class ContentRewardsProvider(ContentSourceProvider):
    """Placeholder implementation — see module docstring. Never makes a
    real network call to contentrewards.com; `download_video` writes a
    real, short, ffmpeg-generated test video (same "real file, not a fake"
    testing philosophy as scripts/verify_production_pipeline.sh) so
    downstream code has a genuinely valid video to work with, falling back
    to a clearly-labeled placeholder text file if the optional `rendering`
    extra (imageio-ffmpeg) isn't installed — same honest-fallback pattern
    as video_clipping/providers/null_clip_renderer.py."""

    def list_available_videos(self) -> list[RemoteCampaignVideo]:
        return list(_PLACEHOLDER_VIDEOS)

    def download_video(self, video: RemoteCampaignVideo, destination_path: str) -> None:
        try:
            import imageio_ffmpeg
        except ImportError:
            logger.warning(
                "content_rewards_provider_placeholder_download_no_ffmpeg",
                external_id=video.external_id,
            )
            with open(destination_path, "w") as f:
                f.write(
                    f"PLACEHOLDER — Content Rewards Milestone 1, no real video "
                    f"(external_id={video.external_id}, imageio-ffmpeg not installed)\n"
                )
            return

        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        duration_s = video.duration_s or 6.0
        subprocess.run(
            [
                ffmpeg_bin, "-y",
                "-f", "lavfi", "-i", f"color=c=blue:s=1280x720:r=25:d={duration_s}",
                "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:d={duration_s}",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                destination_path,
            ],
            check=True,
            capture_output=True,
        )
        logger.info(
            "content_rewards_provider_placeholder_video_generated",
            external_id=video.external_id,
            destination_path=destination_path,
        )
