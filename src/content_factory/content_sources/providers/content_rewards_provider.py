"""Real Content Rewards provider — built from actual browser DevTools
captures (Milestone 2 discovery), not documentation, since Content Rewards
has no public API.

What was actually found, captured live from a real logged-in browser
session at contentrewards.com/discover:

- The page is a Next.js App Router site. Its own internal navigation
  requests return React Server Component "flight" data, not clean JSON —
  but a plain page load's response text still contains one straightforward
  JSON object with the exact shape this provider parses:
  `{"bannerCampaigns": [...], "featuredCampaigns": [...],
  "featuredMixCampaigns": [...], "success": true}`. No login/cookies are
  required to read this listing.
- There is no per-campaign "get video" or "get download link" API.
  Clicking into a campaign leaves contentrewards.com entirely and lands on
  a separate Whop community (each campaign is its own Whop business) whose
  actual footage is delivered one of two incompatible ways depending on
  the brand running it: (a) a public link (commonly a Google Drive folder)
  pasted directly into the campaign's own free-text `description`, or
  (b) a locked Whop mini-app that requires personally joining that specific
  campaign first. (b) cannot be automated generically — there is no
  uniform "list of campaigns' videos" this provider can offer. This
  provider therefore only lists campaigns matching (a), downloading
  through the public (API-key-only, no OAuth) Google Drive API, and
  silently skips every campaign of kind (b) rather than guessing.

Real, unverified-by-this-session risk: contentrewards.com is
Cloudflare-protected, and every non-browser tool tried against it this
session (WebFetch, curl) got HTTP 403. The captures above all came from a
real, logged-in browser, which Cloudflare let through. Whether a plain
server-side `httpx` request also gets through is genuinely unknown until
someone runs it for real (see docs/CONTENT_REWARDS_CONNECTOR.md's
verification workflow) — if Cloudflare blocks it, `list_available_videos`
surfaces that as `ProviderRequestRejected`/`RetryableProviderError` like
any other provider failure, not a crash.
"""

import json
import os
import re

from content_factory.content_sources.base import ContentSourceProvider, RemoteCampaignVideo
from content_factory.retry import ProviderRequestRejected, RetryableProviderError, describe_http_error

_DEFAULT_MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB — matches config.py's default


class _DownloadExceededSizeLimit(Exception):
    """Internal signal only — download_video() always converts this to a
    ProviderRequestRejected after cleaning up the partial file, never lets
    it escape as-is."""


_DISCOVER_URL = "https://contentrewards.com/discover"
_DRIVE_FOLDER_RE = re.compile(r"https://drive\.google\.com/drive/folders/[\w-]+")
_DRIVE_FOLDER_ID_RE = re.compile(r"/folders/([\w-]+)")
# A plain httpx client has no browser TLS/JS fingerprint - identifying as a
# real browser's User-Agent is the only lever available to this provider;
# it does not defeat or work around Cloudflare's actual bot-detection logic.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_CAMPAIGN_LIST_KEYS = ("bannerCampaigns", "featuredCampaigns", "featuredMixCampaigns")


class ContentRewardsProvider(ContentSourceProvider):
    def __init__(self, google_drive_api_key: str = "", max_video_bytes: int = _DEFAULT_MAX_VIDEO_BYTES) -> None:
        self._google_drive_api_key = google_drive_api_key
        self._max_video_bytes = max_video_bytes

    def list_available_videos(self) -> list[RemoteCampaignVideo]:
        httpx = _import_httpx()

        try:
            response = httpx.get(
                _DISCOVER_URL,
                headers={"User-Agent": _BROWSER_USER_AGENT},
                timeout=30.0,
                follow_redirects=True,
            )
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("Content Rewards discover page request timed out") from exc

        if response.status_code >= 500:
            raise RetryableProviderError(
                f"Content Rewards discover page returned {response.status_code}"
            )
        if response.status_code >= 400:
            raise ProviderRequestRejected(
                f"Content Rewards discover page returned {response.status_code}: "
                f"{describe_http_error(response)}"
            )

        campaigns = _extract_campaigns(response.text)

        videos = []
        for campaign in campaigns:
            external_id = campaign.get("id")
            drive_url = _extract_drive_folder(campaign.get("description") or "")
            if not external_id or not drive_url:
                continue
            videos.append(
                RemoteCampaignVideo(
                    external_id=str(external_id),
                    title=str(campaign.get("title") or ""),
                    campaign_name=str(campaign.get("brand") or ""),
                    duration_s=None,
                    download_url=drive_url,
                    source_page_url=_DISCOVER_URL,
                )
            )
        return videos

    def download_video(self, video: RemoteCampaignVideo, destination_path: str) -> None:
        if not self._google_drive_api_key:
            raise RuntimeError(
                "ContentRewardsProvider.download_video requires GOOGLE_DRIVE_API_KEY to be "
                "configured (a Google Drive API key, not OAuth - only needed to read publicly "
                "shared folders)"
            )
        httpx = _import_httpx()

        folder_id_match = _DRIVE_FOLDER_ID_RE.search(video.download_url)
        if not folder_id_match:
            raise ProviderRequestRejected(f"Not a Google Drive folder URL: {video.download_url}")
        folder_id = folder_id_match.group(1)

        try:
            list_response = httpx.get(
                "https://www.googleapis.com/drive/v3/files",
                params={
                    "q": f"'{folder_id}' in parents and mimeType contains 'video/'",
                    "key": self._google_drive_api_key,
                    "fields": "files(id,name,mimeType)",
                },
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("Google Drive folder listing timed out") from exc

        if list_response.status_code >= 500:
            raise RetryableProviderError(
                f"Google Drive folder listing returned {list_response.status_code}"
            )
        if list_response.status_code >= 400:
            raise ProviderRequestRejected(
                f"Google Drive folder listing returned {list_response.status_code}: "
                f"{describe_http_error(list_response)}"
            )

        files = list_response.json().get("files", [])
        if not files:
            raise ProviderRequestRejected(f"No video files found in Google Drive folder {folder_id}")
        file_id = files[0]["id"]

        # Streamed, not `httpx.get` + `.content`: a Google Drive folder is
        # external, uncontrolled content (unlike a manual upload through our
        # own form), so nothing here is allowed to load an unbounded amount
        # of it into memory or onto disk. Content-Length (when the server
        # sends one) is checked up front to reject oversized files without
        # downloading a single byte; actual bytes received are also counted
        # during streaming either way, since a missing or dishonest
        # Content-Length can't be trusted to enforce the limit by itself.
        try:
            with httpx.stream(
                "GET",
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                params={"key": self._google_drive_api_key, "alt": "media"},
                timeout=120.0,
            ) as download_response:
                if download_response.status_code >= 500:
                    raise RetryableProviderError(
                        f"Google Drive file download returned {download_response.status_code}"
                    )
                if download_response.status_code >= 400:
                    raise ProviderRequestRejected(
                        f"Google Drive file download returned {download_response.status_code}"
                    )

                content_length = download_response.headers.get("content-length")
                if content_length is not None and int(content_length) > self._max_video_bytes:
                    raise ProviderRequestRejected(
                        f"Google Drive file {file_id} is {content_length} bytes, exceeding the "
                        f"{self._max_video_bytes}-byte limit (MAX_CONTENT_SOURCE_VIDEO_BYTES)"
                    )

                bytes_written = 0
                try:
                    with open(destination_path, "wb") as f:
                        for chunk in download_response.iter_bytes():
                            bytes_written += len(chunk)
                            if bytes_written > self._max_video_bytes:
                                raise _DownloadExceededSizeLimit()
                            f.write(chunk)
                except _DownloadExceededSizeLimit:
                    if os.path.exists(destination_path):
                        os.remove(destination_path)
                    raise ProviderRequestRejected(
                        f"Google Drive file {file_id} exceeded the {self._max_video_bytes}-byte "
                        f"limit (MAX_CONTENT_SOURCE_VIDEO_BYTES) while streaming - partial file "
                        f"removed"
                    ) from None
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("Google Drive file download timed out") from exc


def _import_httpx():
    try:
        import httpx

        return httpx
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "ContentRewardsProvider requires the 'content_rewards' extra: "
            "pip install '.[content_rewards]'"
        ) from exc


def _extract_campaigns(raw_text: str) -> list[dict]:
    """Locate and parse the one JSON object this provider actually needs out
    of the page's full response text — the campaign-list payload always has
    the same three top-level keys (see module docstring). Handles both a
    plain page load (JSON embedded, escaped, inside a Next.js
    `self.__next_f.push(...)` script chunk) and the raw flight-text shape a
    client-side navigation request returns (JSON already unescaped)."""
    # Anchor on the bare key name, not `{"bannerCampaigns":` - that literal
    # substring doesn't survive HTML-escaping (a plain page load embeds this
    # JSON inside a JS string literal, so its quotes become `\"`), but `{`
    # itself is never escaped either way, so the nearest `{` before the key
    # name is reliably this object's own opening brace.
    key_index = raw_text.find("bannerCampaigns")
    if key_index == -1:
        raise ProviderRequestRejected(
            "Content Rewards discover page did not contain the expected campaign "
            "list data - the site's markup may have changed since this provider "
            "was written"
        )
    start = raw_text.rfind("{", 0, key_index)
    if start == -1:
        raise ProviderRequestRejected(
            "Content Rewards discover page did not contain the expected campaign "
            "list data - the site's markup may have changed since this provider "
            "was written"
        )

    depth = 0
    end = None
    for i in range(start, len(raw_text)):
        char = raw_text[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ProviderRequestRejected("Content Rewards discover page campaign data was truncated")

    payload_text = raw_text[start:end]
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        try:
            payload = json.loads(payload_text.encode().decode("unicode_escape"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderRequestRejected(
                "Content Rewards discover page campaign data could not be parsed as JSON"
            ) from exc

    campaigns: list[dict] = []
    for key in _CAMPAIGN_LIST_KEYS:
        campaigns.extend(payload.get(key) or [])
    return campaigns


def _extract_drive_folder(description: str) -> str | None:
    match = _DRIVE_FOLDER_RE.search(description)
    return match.group(0) if match else None
