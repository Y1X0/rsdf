# External Provider Verification Plan

Production Hardening Sprint H7 — the production readiness review's EP1
finding: the TikTok, YouTube, and Instagram providers (both publishing
and metrics ingestion) are structurally complete and unit-tested against
mocked HTTP responses (`tests/unit/test_publishing_providers.py`,
`tests/unit/test_analytics_ingestion_providers.py`), but — as
`ARCHITECTURE.md` §0/§13 already flagged at design time — **none has ever
been exercised against a live platform API**, because none of app review
(TikTok, Instagram) or a quota increase (YouTube) has been completed.
This document is the plan for closing that gap when real credentials
become available. It does not change, and does not require changing, a
single provider interface — see §5.

## 0. Scope and non-goals

In scope: a concrete, ordered checklist per platform, covering both
sandbox/test-mode verification (no real audience impact) and the
subsequent live/production verification (real audience impact, so gated
behind explicit sign-off).

Out of scope, deliberately: actually completing app review, obtaining
credentials, or running the checklist against a live account — none of
that is possible from this environment (no real platform accounts, no
outbound access to these platforms' APIs in this sandbox, and app review
timelines are measured in weeks, per §13). This document is the runbook
an operator follows once those prerequisites exist, not a substitute for
them.

## 1. What's implemented today, per provider (the honest baseline)

Every provider below is a **simplified single-call implementation**, not
the platform's full real-world flow — each provider's own docstring
already says so; this table exists so the gap is visible in one place
rather than six separate files:

| Platform | Publishing (`publishing/providers/`) | Analytics (`analytics_ingestion/providers/`) |
|---|---|---|
| TikTok | Single `PULL_FROM_URL` init/publish call. Real API's full flow is init → poll upload status → publish; this skips the poll step and assumes the pulled URL is immediately fetchable by TikTok's servers. | Single `video/query/` call for view/like/comment/share counts by video ID. |
| YouTube | Metadata-only call against `upload/youtube/v3/videos` — assumes `request.asset_url` is *already* reachable by Google's upload endpoint. Real API requires a resumable multipart upload (the actual video bytes), not a URL reference. | Single `videos.list?part=statistics` call by video ID. |
| Instagram | Single container-create-and-publish call. Real API is a three-step flow: create a media container → poll `status_code` until `FINISHED` → publish the container. This skips the poll and assumes the container is immediately publishable. | Single `/insights` call for plays/likes/comments/shares/saves by media ID. |

All six: lazy `httpx` import (gated behind the `publishing` extra), retry
only on 5xx/timeout via `publishing/retry.py::call_with_retry` (raises
immediately on 4xx — a bad request or expired token retrying wouldn't
help), and are selected by `publishing/factory.py` /
`analytics_ingestion/factory.py` only when both the platform's client
credentials *and* the specific account's decrypted access token are
present — otherwise the always-available `ManualPublishingProvider` /
`ManualAnalyticsProvider` is used, exactly as today.

**The upload-mechanism gap (TikTok's poll step, YouTube's real upload,
Instagram's poll step) is the single biggest risk carried into live
verification** — see each platform's §2-4 "Known gap" callout below. Treat
closing it as a prerequisite to the *live* checklist, not something to
discover during it.

## 2. TikTok

**Prerequisites** (§13): a TikTok Developer app, Content Posting API
scope, and — for anything beyond private/draft posting — **app audit**,
which the platform itself budgets in weeks, not days.

**Sandbox verification** (before/during app audit, no public-audience
risk):
1. Register a TikTok Developer app; obtain a client key/secret and
   complete OAuth for one test creator account (`TIKTOK_CLIENT_KEY`/
   `TIKTOK_CLIENT_SECRET`, then that account's OAuth token through
   `POST /accounts` → `oauth_token`, encrypted via
   `services/token_encryption.py`).
2. Publish one video with `PublishRequest.asset_url` pointing at a
   short, policy-safe test clip, through the real code path
   (`POST /videos/{id}/publish`) — while the app is unaudited, this
   posts as **private/draft only** (a platform-side restriction, not a
   flag in this codebase).
3. Confirm in the TikTok Developer dashboard (or the creator's own
   private drafts) that the post actually landed, and that its caption
   contains the disclosure flag (`disclose_ai_generated`) correctly set
   from `contains_ai_voice`/`contains_ai_visual`.
4. Call `GET /publications/{id}` and confirm `external_post_id` matches
   the platform's own post ID.
5. **Known gap to verify specifically**: does TikTok's `PULL_FROM_URL`
   source actually fetch `asset_url` synchronously within the API call,
   or does it need the polling step this implementation skips? If the
   real behavior is asynchronous, `publish()` needs a poll-until-ready
   step added before this goes live — confirm this before trusting
   `PublishResult.published=True` as "the video is actually up."
6. Trigger `POST /publications/{id}/metrics/sync` against that same
   post; confirm the returned view/like/comment/share counts match what
   the TikTok dashboard shows for it.
7. Verify the 4xx/5xx split: revoke the OAuth token and confirm a
   publish attempt fails immediately (not retried) with a clear error;
   separately, confirm (via TikTok's own status page or a rate-limit
   response) that a 5xx/timeout is retried per `retry.py`'s 3-attempt
   backoff.

**Live verification** (after app audit passes, real-audience risk —
requires explicit sign-off before running):
1. Repeat steps 2-6 above against a real (non-draft) public post on a
   real creator account, with the account's actual `daily_post_cap` and
   health tier in effect (i.e., through the normal publishing_service
   gating, not a bypass).
2. Confirm `PUBLISHING_ENABLED=false` actually blocks the attempt (the
   kill-switch), then re-enable and confirm it proceeds — proves the
   emergency rollback path works before it's ever needed for real.
3. Watch the account's health/strikes for at least 24h after the first
   few live posts — TikTok's own policy enforcement is the thing this
   codebase cannot simulate.

## 3. YouTube

**Prerequisites** (§13): a Google Cloud project with the YouTube Data API
v3 enabled, OAuth consent screen configured, and — practically — a quota
increase request, since the default ~10,000 units/day allows roughly 6
uploads/day/project (~1,600 units each) before hitting the ceiling.

**Sandbox verification**:
1. Register the OAuth client (`YOUTUBE_CLIENT_ID`/`YOUTUBE_CLIENT_SECRET`),
   complete OAuth for one test channel, attach its token via
   `POST /accounts`.
2. **Known gap to verify first, before anything else**: `publish()` as
   implemented sends *metadata only* to `upload/youtube/v3/videos` — it
   does not upload video bytes. Confirm what YouTube's API actually does
   with a metadata-only POST to the upload endpoint (very likely: reject
   it, since the real API requires the video payload as part of a
   resumable upload session). **This provider almost certainly needs a
   real multipart/resumable-upload implementation before step 3 can even
   run** — this is the one platform where the gap in §1 blocks
   verification entirely rather than just needing confirmation.
3. Once upload actually works: publish one Short as **`privacyStatus:
   "unlisted"`** (override the current hardcoded `"public"` for this
   verification pass only — do not run step 3 with real `"public"`
   videos), confirm it appears in YouTube Studio, and confirm
   `external_post_id` (the returned `id`) matches.
4. Confirm the AI-disclosure text appended to the description
   (`[Contains AI-generated content]`) actually appears, and separately
   check whether YouTube's own "altered or synthetic content" disclosure
   toggle (a metadata field, not description text) should be set instead
   — a real compliance question to resolve during this pass, not
   something this codebase currently addresses.
5. Sync metrics (`POST /publications/{id}/metrics/sync`) and compare
   against YouTube Studio's own analytics for the same video — note that
   view counts can lag by hours on YouTube's side, so don't treat a `0`
   immediately after upload as a bug.
6. Track quota consumption in the Google Cloud Console against the
   Cost Control Layer's `provider_quota_usage` table (ARCHITECTURE.md
   §10b) — confirm the two numbers agree, since a mismatch means the
   count-based ceiling isn't tracking real usage correctly.

**Live verification** (after quota increase, if needed, and once upload
actually works):
1. Repeat with `privacyStatus: "public"` on a real channel.
2. Same kill-switch and cadence-cap checks as TikTok's live §2.1-§2.2.
3. Watch quota consumption under real usage patterns for at least a
   week before assuming the configured daily budget is realistic.

## 4. Instagram

**Prerequisites** (§13): an Instagram Professional account linked to a
Facebook Page, a Meta app with the Instagram Graph API's Content
Publishing permission, and Meta App Review for any use beyond the
app's own registered test users — plus platform-enforced limits of
roughly 25-100 posts per 24h per account.

**Sandbox verification** (using Meta's Graph API Explorer / registered
test users, before App Review):
1. Register the Meta app (`INSTAGRAM_APP_ID`/`INSTAGRAM_APP_SECRET`),
   add a test user with Instagram Professional access, attach its token.
2. **Known gap to verify**: this implementation calls
   `{account_id}/media_publish` directly with `video_url` — the real
   Content Publishing flow is (a) create a media container via
   `POST /{account_id}/media`, (b) poll `GET /{container_id}?fields=
   status_code` until it's `FINISHED` (Instagram-side video processing
   takes real time), then (c) `POST /{account_id}/media_publish` with
   the container ID. Confirm whether skipping the create+poll steps and
   calling `media_publish` directly with a raw `video_url` actually
   works against the real API, or errors — if it errors, the create/poll
   steps need to be added before this provider can publish anything.
3. Once a container/post is created: confirm it appears on the test
   account, confirm the caption's disclosure hashtags
   (`#ad #AIgenerated`) render as expected, and confirm
   `external_post_id` (the returned `id`) matches the real media ID.
4. Sync metrics and compare `plays`/`likes`/`comments`/`shares`/`saved`
   against the Graph API Explorer's own `/insights` response for the
   same media ID directly (bypassing this codebase) — confirms the field
   mapping in `instagram_provider.py::fetch_metrics` is reading the
   right keys.
5. Confirm 4xx (e.g., an expired token, or a caption that trips Meta's
   own content policy) fails immediately without retrying, and that a
   5xx/timeout is retried per `retry.py`.

**Live verification** (after Meta App Review, real-audience risk):
1. Repeat against a real Professional account with real posting cadence
   in effect.
2. Same kill-switch and 24h health-monitoring checks as TikTok's live
   §2.1-§2.3 — Meta's policy enforcement (like TikTok's) is the thing
   this codebase cannot simulate ahead of time.

## 5. Why none of this requires a provider-interface change

Every check above exercises the *existing* `PublishingProvider`/
`PlatformAnalyticsProvider` ABCs and the *existing*
`publishing_service.py`/`metrics_sync_service.py` orchestration — going
from "unverified" to "verified" is entirely a matter of:

- provisioning real credentials (env vars only — `TIKTOK_CLIENT_KEY`
  etc., already defined in `config.py` and `.env.example`),
- an account's OAuth token (through the existing `POST /accounts`
  endpoint and `token_encryption.py`, unchanged),
- and, if any "known gap" above turns out to block verification (most
  likely YouTube's upload mechanism, possibly Instagram's poll step),
  implementing the missing real-API step **inside** that platform's
  existing provider class, without touching its constructor signature,
  its `publish()`/`fetch_metrics()` method signature, the ABC it
  implements, or `publishing/factory.py`'s selection logic.

`ManualPublishingProvider`/`ManualAnalyticsProvider` remain the default
and the safety net throughout every stage above: if a platform's real
provider fails verification (or app review is denied, or credentials
aren't provisioned), the system's behavior is identical to today —
publish requests are recorded as "ready to publish manually," metrics
stay manual-entry-only. Nothing about verification is a precondition for
the rest of the system working.

## 6. Sign-off checklist (all three platforms, before flipping to live)

- [ ] Sandbox checklist fully passed for this platform (§2/§3/§4).
- [ ] Any "known gap" specific to this platform resolved or explicitly
      accepted as a follow-up with a tracked owner.
- [ ] `PUBLISHING_ENABLED` kill-switch verified to actually block/unblock.
- [ ] Cadence cap and account-health gating verified against a real
      account (not just the existing unit tests' fakes).
- [ ] Metrics sync cross-checked against the platform's own dashboard at
      least once.
- [ ] A human (not this document) has explicitly approved moving this
      platform from sandbox to live for a specific account.
