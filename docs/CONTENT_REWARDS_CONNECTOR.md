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

## Milestone 2 (not started): the real provider

To move from the placeholder to a real integration:

1. Log into `https://contentrewards.com/discover` in a real browser with
   DevTools open (F12 → Network tab).
2. Trigger the actions "list my available campaigns" and "view/download a
   campaign's video" in the UI, and capture the real request(s) the page
   itself makes: URL, method, request headers' *names* (not values),
   and the response JSON shape.
3. **Never paste a real cookie, `Authorization` header value, or access
   token into a chat/AI session** — same rule already applied to every
   other credential this project handles (`AUTH_CLIENT_SECRET`,
   `IG_ACCESS_TOKEN`). Hand over the request/response *shape* only; the
   real secret value goes directly into Render's Environment tab (or a
   GitHub Actions secret for the verification workflow), never here.
4. `providers/content_rewards_provider.py`'s `list_available_videos()`/
   `download_video()` get rewritten to make those exact real `httpx`
   calls — `content_sources/base.py`'s interface, `factory.py`'s
   selection logic, and every caller (the sync endpoint,
   `clip_service.register_source_video`) stay exactly as they are.
5. Real verification reuses the same `workflow_dispatch`-only GitHub
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
