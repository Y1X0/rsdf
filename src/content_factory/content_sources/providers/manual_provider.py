from content_factory.content_sources.base import ContentSourceProvider, RemoteCampaignVideo


class ManualContentSourceProvider(ContentSourceProvider):
    """Default, zero-dependency: no external videos are ever available.
    Matches every other package's safe-default convention
    (NullTranscriptionProvider, ManualPublishingProvider) — sourcing stays
    exactly as manual as it is today (`POST /source-videos` upload) until a
    real content-source provider is explicitly configured."""

    def list_available_videos(self) -> list[RemoteCampaignVideo]:
        return []

    def download_video(self, video: RemoteCampaignVideo, destination_path: str) -> None:
        raise NotImplementedError("ManualContentSourceProvider has no videos to download")
