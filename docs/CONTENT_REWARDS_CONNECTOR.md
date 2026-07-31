# Content Rewards Connector

Automates the one manual step still left in the pipeline: sourcing a
long-form video. Everything downstream of a `SourceVideo` row existing
with a real `storage_path` — transcription, AI clip selection, ffmpeg
rendering, human review, Instagram publish — already works end-to-end in
production (`docs/PRODUCTION_MILESTONES.md`) and is untouched by this
connector.

## What this is

`content_sources/` is a provider package, structured exactly like every
other external integration in this codebase (`publishing/`,
`analytics_ingestion/`, `transcription/`): an ABC
(`ContentSourceProvider`), a factory (`get_content_source_provider`), and
provider implementations behind it. `POST /source-videos/sync-content-rewards`
calls the currently-configured provider, and registers each video it
returns exactly the way `POST /source-videos` (the existing manual upload
route) already does — a fetched video enters the transcribe → analyze →
render → review → publish pipeline identically to an uploaded one, with
zero changes to any of those existing routes/services.

## Current status: Milestone 1 (placeholder, not yet real)

**Content Rewards (`contentrewards.com`) has no confirmed public,
creator-facing API.** Verified this session: the site itself returns
HTTP 403 to unauthenticated requests (Cloudflare-protected), and while
Whop (the platform Content Rewards is hosted on) does have a real,
documented public API (`whopsdk-python`, API-key auth), its resources are
commerce-level (Payments, Companies, Memberships, Users) — nothing
campaign- or content-submission-specific.

**Chosen path (confirmed with the project owner): reverse-engineer the
site's own internal API**, captured from the browser's DevTools Network
tab while logged in, rather than Playwright browser automation — lighter,
no new heavy dependency/system libs in the Docker image, at the accepted
cost of relying on an undocumented surface that can change without
notice.

Until those exact requests are captured, `CONTENT_SOURCE_PROVIDER=content_rewards`
selects `ContentRewardsProvider` — a **clearly-labeled placeholder**:
`list_available_videos()` returns two synthetic, `[PLACEHOLDER]`-titled
entries, and `download_video()` generates a real, tiny, genuinely playable
ffmpeg video for each (not fake bytes) rather than making any real network
call. This exists so the rest of the connector — the DB columns, the
idempotent sync endpoint, the fact that a fetched video really does flow
through the unmodified pipeline — is built and verified today, without
waiting on real credentials.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `CONTENT_SOURCE_PROVIDER` | `manual` | `"manual"` (default, no-op — sourcing stays exactly as manual as it is today) or `"content_rewards"` (Milestone 1's placeholder; a real provider in a later milestone) |

## Milestone 2 (in progress): API discovery

To move from the placeholder to a real integration, the exact internal
requests Content Rewards' own web app makes have to be captured first —
`scripts/discover_content_rewards_api.py` does this passively and safely,
without ever writing a cookie/token/secret value to disk or chat, and
without any Cloudflare bypass (a real human solves any real challenge, in
a real browser, visually).

**Discovery-only device requirement: none.** This works from a phone
alone via a Codespace, since GitHub Codespaces has no display server by
default and a scripted/blind login can't be built without knowing the
site's real form markup in advance. The procedure instead gives you a
real, interactive, headed Chromium window — viewable and controllable
from your phone's own browser via VNC — so you log in exactly like a
normal visitor while the script listens in the background.

### One-time Codespace setup

```bash
# System packages for a virtual display + VNC-over-HTTP:
sudo apt-get update && sudo apt-get install -y xvfb x11vnc novnc websockify

# Playwright + a real Chromium build:
pip install playwright
playwright install --with-deps chromium

# Start a virtual display, then a VNC server pointed at it, then noVNC
# (a browser-based VNC client) bridging it to plain HTTP/WebSocket:
Xvfb :99 -screen 0 1280x800x24 &
export DISPLAY=:99
x11vnc -display :99 -forever -nopw -quiet -rfbport 5900 &
websockify --web=/usr/share/novnc 6080 localhost:5900 &
```

Then, in the Codespace's **Ports** tab: forward port `6080`, set its
visibility to **Private** (so only your own GitHub login can reach it —
this is the access control, since `-nopw` alone has none), and open the
forwarded URL suffixed with `/vnc.html?autoconnect=true&resize=remote` in
your phone's browser. You'll see a live view of whatever runs on the
Codespace's virtual display.

### Running the capture

In the Codespace's terminal (a second phone browser tab, e.g. via
vscode.dev or the Codespace web UI):

```bash
export DISPLAY=:99
python scripts/discover_content_rewards_api.py
```

A real Chromium window opens on the virtual display (visible in your VNC
tab) already pointed at `contentrewards.com/discover`. In that window:

1. Log in normally. If Cloudflare shows a challenge, solve it yourself,
   visually — that's a real human passing a real challenge, not a bypass.
2. Open your campaigns/discover list.
3. Open one campaign's video and its download link.

Switch back to the terminal tab and press **Ctrl+C** when done. The
script writes `content_rewards_discovery.jsonl` (repo root, gitignored) —
each line is one matching request/response already reduced to: URL,
method, header **names only** (sensitive-looking ones flagged, values
never recorded), and body **shape** (JSON key names and types, never real
values). The browser's own session data lives only in
`.content_rewards_browser_profile/` (also gitignored) — nothing from
either file is ever committed.

Read `content_rewards_discovery.jsonl` yourself, confirm it holds no real
values, and paste **its contents** back into chat. **Never** paste a raw
cookie, `Authorization` header value, or access token here or anywhere
else — same rule already applied to every other credential this project
handles (`AUTH_CLIENT_SECRET`, `IG_ACCESS_TOKEN`).

### After discovery: building the real provider (not started)

Once the real request/response shapes are known:

1. `providers/content_rewards_provider.py`'s `list_available_videos()`/
   `download_video()` get rewritten to make those exact real `httpx`
   calls — `content_sources/base.py`'s interface, `factory.py`'s
   selection logic, and every caller (the sync endpoint,
   `clip_service.register_source_video`) stay exactly as they are.
2. Whatever credential the real calls need (a session cookie or a
   recorded auth header) goes directly into Render's Environment tab (or
   a GitHub Actions secret for the verification workflow) — never typed
   into chat.
3. Real verification reuses the same `workflow_dispatch`-only GitHub
   Actions pattern as `verify-production.yml`/
   `check-production-health.yml`: a new `sync-content-rewards.yml`,
   manual trigger only, using a GitHub repository secret for whatever
   credential the real requests need.

## Security notes

- Whatever credential Milestone 2 needs (a session cookie or a recorded
  auth header) is a real secret — same handling as every other credential
  in this project: never in chat, only in Render's Environment tab or a
  GitHub Actions secret.
- A session cookie/password is equivalent to full account access — more
  sensitive than a typical API key. Store it encrypted at rest via
  `services/token_encryption.py` (the same Fernet-based encryption already
  protecting `OwnedAccount.encrypted_oauth_token`) rather than a plaintext
  config field.
- The real provider must validate a downloaded file is actually a video
  (e.g. via `ffprobe`) before it's ever handed to transcription/render —
  an expired/broken session could otherwise silently download an HTML
  login page instead of a video.
- Rate-limit/back off the connector's own requests (reuse `retry.py`'s
  `RetryableProviderError`/`describe_http_error`, exactly like every other
  real provider in this codebase) — scraping too aggressively risks the
  account being flagged, a real business risk to guard against by design.
