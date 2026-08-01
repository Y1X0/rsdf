"""Ensures a SourceVideo's local media file actually exists before any
pipeline stage that reads it from disk (transcribe today; render, if it
ever needs to re-read the source file directly, can reuse this too).

Real production gap this closes: this service's local filesystem has no
persistent Disk on its current hosting plan - a spin-down/restart gives
it a brand-new, empty filesystem at any time, entirely independent of any
code deploy. A SourceVideo whose storage_path was set by a previously
successful sync/upload can therefore point at a file that no longer
exists by the time a later request (transcribe) tries to read it -
previously an unhandled FileNotFoundError deep inside the transcription
provider, surfaced to callers as an opaque 500 with no clear cause.

This is deliberately a separate, reusable helper rather than logic living
directly inside the transcribe endpoint (or duplicated from
api/routers/clips.py's sync_content_rewards): any current or future
pipeline stage that reads a SourceVideo's file can call it first.
"""

import re
import time
from pathlib import Path

from sqlalchemy.orm import Session

from content_factory.agents.base import agent_run
from content_factory.content_sources.base import ContentSourceProvider
from content_factory.db.models.enums import SourceVideoOrigin
from content_factory.db.models.source_video import SourceVideo
from content_factory.logging_config import get_logger

logger = get_logger(__name__)

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


class MediaUnavailableError(Exception):
    """Raised when a SourceVideo's file is missing from local storage and
    cannot be automatically recovered. Callers should turn this into a
    clear 4xx response rather than let the original FileNotFoundError
    escape as an opaque 500."""


def ensure_local_media_available(
    db: Session,
    *,
    source_video: SourceVideo,
    content_source_provider: ContentSourceProvider,
    storage_dir: Path,
) -> None:
    """No-op if source_video.storage_path already points at a real, present
    file. Otherwise, attempts to recover by re-fetching from the video's
    original external source - reusing this exact row (same id, same
    external_source_id: never creates a new SourceVideo, never touches
    uq_source_videos_source_external_id). Raises MediaUnavailableError if
    recovery isn't possible - e.g. a manually-uploaded video has no
    external source to re-fetch from, or the remote campaign is no longer
    listed."""
    if source_video.storage_path and Path(source_video.storage_path).exists():
        return

    if source_video.source != SourceVideoOrigin.CONTENT_REWARDS or not source_video.external_source_id:
        raise MediaUnavailableError(
            f"SourceVideo {source_video.id}'s file is missing from local storage "
            f"(storage_path={source_video.storage_path!r}) and cannot be automatically "
            f"recovered: source={source_video.source!r} has no external source to re-fetch from."
        )

    logger.warning(
        "source_video_media_missing_attempting_recovery",
        source_video_id=source_video.id,
        external_source_id=source_video.external_source_id,
        storage_path=source_video.storage_path,
    )

    remote_video = next(
        (
            v
            for v in content_source_provider.list_available_videos()
            if v.external_id == source_video.external_source_id
        ),
        None,
    )
    if remote_video is None:
        raise MediaUnavailableError(
            f"SourceVideo {source_video.id}'s file is missing from local storage and its "
            f"original campaign (external_source_id={source_video.external_source_id!r}) is no "
            "longer listed by the content source - cannot automatically recover."
        )

    storage_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _UNSAFE_FILENAME_CHARS.sub("_", f"{remote_video.external_id}.mp4")
    dest_path = storage_dir / f"{source_video.id}_{safe_name}"

    with agent_run(
        db,
        agent_name="content_rewards_connector",
        scope="source_video.recover_missing_media",
        entity_type="source_video",
        entity_id=source_video.id,
    ) as handle:
        started = time.monotonic()
        content_source_provider.download_video(remote_video, str(dest_path))
        handle.record_output(
            provider="content_rewards",
            model=None,
            model_version=None,
            prompt=remote_video.source_page_url,
            output_summary={"external_id": remote_video.external_id, "recovered": True},
            cost_usd=0.0,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    source_video.storage_path = str(dest_path)
    db.flush()
    logger.info(
        "source_video_media_recovered", source_video_id=source_video.id, storage_path=str(dest_path)
    )
