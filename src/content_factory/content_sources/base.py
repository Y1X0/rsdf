"""The one interface business logic is allowed to depend on for sourcing a
long-form video from somewhere other than a manual multipart upload — same
shape as every other provider package in this codebase
(transcription/base.py, publishing/base.py): an ABC, a factory, a
zero-dependency safe default, and one real implementation behind a lazy
import.

`api/routers/clips.py`'s existing `POST /source-videos` (manual upload)
stays exactly as-is and remains the permanent fallback path — this
interface only adds a second, additive way for a `SourceVideo` row and its
on-disk file to come into existence. Once a video is on disk with a real
`storage_path`, `services/clip_service.py`'s `transcribe_source_video` /
`analyze_source_video` / `render_clip` neither know nor care which path
produced it.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class RemoteCampaignVideo:
    """One video available to fetch from an external content source.
    `external_id` is that platform's own identifier for the video/campaign
    — the dedup key `services/idempotency.py::run_idempotent` uses so a
    repeated sync never re-downloads (or re-registers) the same remote
    video twice."""

    external_id: str
    title: str
    campaign_name: str
    duration_s: float | None
    download_url: str
    source_page_url: str


class ContentSourceProvider(ABC):
    @abstractmethod
    def list_available_videos(self) -> list[RemoteCampaignVideo]:
        """List every video currently available to fetch. Real providers
        should return a plain list (no pagination handling required by
        callers) — a provider with a paginated upstream API is responsible
        for walking every page itself."""
        raise NotImplementedError

    @abstractmethod
    def download_video(self, video: RemoteCampaignVideo, destination_path: str) -> None:
        """Fetch `video`'s actual file to `destination_path` (a full local
        file path, parent directory already created by the caller). Must
        raise rather than silently write a partial/invalid file — callers
        treat a normal return as "a real, complete video now exists at
        destination_path"."""
        raise NotImplementedError
