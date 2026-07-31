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

## Current status: Milestone 3 (real, unverified against the live site)

**Content Rewards (`contentrewards.com`) has no confirmed public,
creator-facing API.** Verified this session: the site itself returns
HTTP 403 to unauthenticated requests from non-browser tools
(Cloudflare-protected), and while Whop (the platform Content Rewards is
hosted on) does have a real, documented public API (`whopsdk-python`,
API-key auth), its resources are commerce-level (Payments, Companies,
Memberships, Users) — nothing campaign- or content-submission-specific.

**What Milestone 2's real browser-based discovery actually found**
(captured live, from a real logged-in browser session — see
`scripts/discover_content_rewards_api.py`):

- `contentrewards.com/discover` is a Next.js App Router page. A plain page
  load's response text embeds one clean JSON object —
  `{"bannerCampaigns": [...], "featuredCampaigns": [...],
  "featuredMixCampaigns": [...], "success": true}` — listing every public
  campaign (brand, budget, pay-per-view rate, free-text `description`,
  ...). **No login or cookies are required to read this.**
- There is no per-campaign "list videos" or "get download link" API.
  Clicking into an actual campaign leaves contentrewards.com entirely and
  lands on a *separate Whop community* — each campaign is its own Whop
  business. From there, footage is delivered one of two incompatible ways
  depending on the brand: (a) a public link (commonly a Google Drive
  folder) pasted directly into the campaign's own `description` text, or
  (b) a locked Whop mini-app that requires personally joining that
  specific campaign first, with no consistent structure across brands.

**Chosen scope (confirmed with the project owner) given that finding:**
`ContentRewardsProvider` only automates case (a) — campaigns whose
description already contains a public Google Drive folder link. Case (b)
campaigns are silently skipped; there is no way to automate those
generically without joining each one individually, so they stay exactly
as manual as they are today.

`list_available_videos()` fetches `/discover` (plain `httpx`, no
auth/cookies) and parses out that JSON object; `download_video()` reads
the matched campaign's Google Drive folder via the public,
API-key-only (no OAuth) Drive API, since these are folders shared "Anyone
with the link can view".

**Real, unverified risk**: every non-browser tool tried against
`contentrewards.com` this session (Claude's own `WebFetch`, `curl`) got
HTTP 403 from Cloudflare. The discovery above all came from a real,
logged-in *browser* session, which Cloudflare let through — whether a
plain server-side `httpx` request also gets through is genuinely unknown
until it's run for real (see Verification below). If Cloudflare blocks
it, `list_available_videos()` raises `ProviderRequestRejected`/
`RetryableProviderError` like any other provider failure — not a crash —
and the sync endpoint's existing `agent_run`/cost-ledger/idempotency
wiring records it like any other failed external call.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `CONTENT_SOURCE_PROVIDER` | `manual` | `"manual"` (default, no-op — sourcing stays exactly as manual as it is today) or `"content_rewards"` (real `ContentRewardsProvider`, scope above) |
| `GOOGLE_DRIVE_API_KEY` | `""` | A Google Cloud API key (not OAuth) used only to read publicly-shared Drive folders. Required for `download_video()`; `list_available_videos()` works without it. |

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

### Milestone 3 (done, this session): the real provider

`providers/content_rewards_provider.py` now makes real requests: a plain
`GET https://contentrewards.com/discover` (no auth), then for each
campaign whose `description` contains a Google Drive folder link, the
public Drive API to list and download the first video file in that
folder. `content_sources/base.py`'s interface, `factory.py`'s selection
logic, and every caller (the sync endpoint, `clip_service.register_source_video`)
are unchanged. Tested this session only against mocked HTTP responses
(`tests/unit/test_content_sources.py`) — never a live network call,
matching this codebase's zero-secrets-required test philosophy.

### Verification (not done yet — needs a real run)

Real verification reuses the same `workflow_dispatch`-only GitHub Actions
pattern as `verify-production.yml`/`check-production-health.yml`: a new
`sync-content-rewards.yml`, manual trigger only, calling
`POST /source-videos/sync-content-rewards` against the live deployment
with `CONTENT_SOURCE_PROVIDER=content_rewards` and a real
`GOOGLE_DRIVE_API_KEY` set, and reporting how many real videos were
found/queued. This is the only way to actually learn whether Cloudflare
blocks the plain `httpx` request — nothing in this session could confirm
that either way.

## Security notes

- `GOOGLE_DRIVE_API_KEY` is a plain API key (not a user credential/OAuth
  token) scoped only to reading files the folder owner already made
  public — same handling as every other API key in this project
  (`GROQ_API_KEY`, `ANTHROPIC_API_KEY`): a Render environment variable,
  never typed into chat.
- The provider does not use or store any Content Rewards/Whop session
  cookie or login credential at all — by design, it only ever reads data
  that requires no authentication. If a future milestone needs to cover
  the locked-mini-app campaigns (case (b) above), that would require a
  real per-campaign membership/session and should reuse
  `services/token_encryption.py` (the same Fernet-based encryption
  already protecting `OwnedAccount.encrypted_oauth_token`) rather than a
  plaintext config field — not attempted here.
- A downloaded file is trusted as-is once the Drive API returns 200; a
  broken/renamed Drive file could in principle not be a real video. This
  mirrors every other provider's assumption in this codebase (no provider
  currently `ffprobe`-validates a downloaded asset) — worth hardening if
  real-world runs show it's an actual problem, not before.
- Rate-limit/back off the connector's own requests (reuse `retry.py`'s
  `RetryableProviderError`/`describe_http_error`, exactly like every other
  real provider in this codebase) — scraping too aggressively risks the
  account being flagged, a real business risk to guard against by design.
