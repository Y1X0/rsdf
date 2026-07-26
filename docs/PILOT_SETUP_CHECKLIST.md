# Pilot Setup Checklist

**Status:** `docs/PILOT_PLAN.md` is approved. Execution is paused — do not run the pipeline, generate simulated results, or fabricate business metrics — until the items below are in place. This document exists so "ready to pilot" is a checked list, not a judgment call.

**Why this exists:** this development/CI environment has no real LLM credentials, no real platform credentials, and no real audience — every provider factory in this codebase (`llm/factory.py`, `video_production/*/factory.py`, `publishing/factory.py`, `analytics_ingestion/factory.py`) falls back to a safe, zero-dependency default (`FakeLLMClient`, `SilentTTSProvider`, `NullRenderer`, `ManualPublishingProvider`/`ManualAnalyticsProvider`) the moment a required credential is missing. That's the correct, intended behavior for development and CI — it is also exactly why nothing produced in this environment can be a real business result. This checklist is what closes that gap.

---

## 1. Required API credentials

| Credential | Env var(s) | Mandatory for the pilot? | What breaks without it |
|---|---|---|---|
| Anthropic API key | `ANTHROPIC_API_KEY` (`LLM_PROVIDER=anthropic`, the default) | **Mandatory.** Without this, `resolved_llm_provider()` silently falls back to `FakeLLMClient`, which returns empty/placeholder JSON — every research brief, idea, and script would be synthetic. There is no pilot without this. | Research briefs, ideas, and scripts are all placeholder text, not real content. |
| ElevenLabs API key | `ELEVENLABS_API_KEY` (`TTS_PROVIDER=elevenlabs`) | **Optional**, but recommended. Without it, `SilentTTSProvider` produces silent audio — the rendered video would have no real voiceover. A pilot can still run with silent/no-voice video if the format doesn't require narration, but that's a real content-quality constraint, not a technical gap. | Videos render with no voiceover. |
| A real render backend | `RENDERER_BACKEND=template_pillow` (no separate key — this uses local Pillow, not a paid API) vs. `RENDERER_BACKEND=null` | **Mandatory** for real output. `NullRenderer` produces a placeholder asset manifest, not an actual playable video file — nothing worth publishing comes out of it. `template_pillow` is already zero-cost (no external API), so there's no credential to obtain here, just a config flip plus verifying it actually produces acceptable output for the pilot's content style. | `NullRenderer` output has nothing to publish. |
| TikTok Content Posting API app credentials | `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` + a per-account OAuth token | **Optional per platform** — see §8/§9 below. Only needed for the platform(s) the pilot will actually publish to via automation. | Falls back to `ManualPublishingProvider` for that platform — not broken, just manual (see §3). |
| YouTube Data API v3 credentials | `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` + a per-account OAuth token | **Optional per platform.** | Same fallback as above. |
| Instagram Graph API credentials | `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET` + a per-account OAuth token | **Optional per platform.** | Same fallback as above. |
| Whop campaign access | No API integration exists — see §4 | **N/A as a credential** — this is a process gap, not a missing key (see §4). | N/A |
| Slack webhook or SMTP (budget/review alerts) | `SLACK_WEBHOOK_URL` or `SMTP_HOST`/`SMTP_FROM_ADDR`/`SMTP_TO_ADDR` | **Optional.** Without it, `NotificationProvider` falls back to structured-log-only notifications — budget alerts still fire, just to the log stream instead of a channel a human is actually watching. | Alerts are log-only; someone must actually be tailing logs. |
| Sentry DSN | `SENTRY_DSN` | **Optional**, recommended for a real pilot given real users/spend are involved. | No error aggregation; falls back to structured logs only (still functional). |
| AWS credentials (media backup) | `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION` (boto3's own standard vars, not app-specific) + `MEDIA_BACKUP_ENABLED=true` + `MEDIA_BACKUP_S3_BUCKET` | **Optional.** Local disk remains the primary store either way (see §6's storage note). | No off-host backup copy of rendered assets. |

---

## 2. Required environment variables (beyond credentials)

These have safe defaults for development but need a deliberate, real value before a pilot, not just "whatever's already there":

| Variable | Pilot value | Why |
|---|---|---|
| `ENVIRONMENT` | `production` | Activates `Settings.validate_production_safety()` — refuses to start if `DATABASE_URL` is SQLite or `JWT_SECRET_KEY` is missing/short. This is the single check that catches "someone forgot to point at real Postgres" before it causes silent data loss. |
| `DATABASE_URL` | A real Postgres connection string (managed service strongly recommended — see `docs/DATABASE_OPERATIONS.md` §1) | SQLite is a dev/test-only fallback; a pilot's campaign/video/cost/revenue data needs a real, backed-up database from the first row written. |
| `JWT_SECRET_KEY` | A real, 32+ character random secret (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) | Below 32 characters, boot-time validation refuses to start under `ENVIRONMENT=production`. Never reuse the value from any `.env.example` or test fixture. |
| `AUTH_CLIENT_ID` / `AUTH_CLIENT_SECRET` | Real, pilot-specific values, not the repo's test literals (`test-operator`/`test-operator-secret`) | These gate who can call the API at all — reusing test credentials in a real deployment is a real credential leak, not a hardening gap. |
| `TOKEN_ENCRYPTION_KEY` | A real Fernet key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) | **Mandatory the moment any real platform OAuth token is stored** (`POST /accounts` with `oauth_token`). Without it, that call fails closed with a clear 500 — correct behavior, but plan for it rather than discover it mid-pilot. |
| `WEB_CONCURRENCY` | `1`, unless `RATE_LIMIT_BACKEND=redis` and `REDIS_URL` are also set | Raising worker count without Redis silently multiplies the effective auth rate limit — see `docs/DEPLOYMENT.md` §6. At pilot scale, `WEB_CONCURRENCY=1` is almost certainly sufficient; don't raise it without a reason. |
| `PUBLISHING_ENABLED` | `true` (the default), with a known procedure to flip it to `false` | This is the pilot's fastest rollback lever — see `docs/PILOT_PLAN.md` §3's rollback section. Confirm whoever runs the pilot knows this exists before day one, not after an incident. |
| `MEDIA_STORAGE_DIR` | A real, persistent volume path (not an ephemeral container filesystem default) | If the deployment target is containerized, confirm this path survives a redeploy — see `docs/PRODUCTION_HARDENING_REPORT.md` §3's still-open "media storage is still local-disk-primary" risk. |

---

## 3. Whop campaign access requirements

Per `ARCHITECTURE.md`'s own open question (§0, §13): **there is no confirmed public Whop Content Rewards API** for campaign discovery or submission tracking. This is not a code gap this system can close — it's an external, unconfirmed dependency. Before the pilot:

- [ ] Confirm directly with Whop (or from firsthand account access) exactly how campaign briefs, submission requirements, and payout terms are obtained today — dashboard access, a contact, an export, whatever it actually is.
- [ ] Confirm how submission/approval status and earnings data actually get reported back — this determines how `POST /videos/{id}/revenue` gets real numbers (manual entry from whatever Whop actually shows, on whatever cadence Whop actually updates it) rather than assumed/estimated figures.
- [ ] Have at least 2-3 real, currently-open Whop campaigns identified and read *before* the pilot starts (real brand, real `rules_text`, real payout model) — `POST /campaigns` already accepts this data via manual entry; there is no automation to build here, only real inputs to gather.
- [ ] Confirm someone has authority/access to actually submit pilot videos into Whop's system once produced — content sitting approved-and-rendered with nowhere to submit isn't a pilot result.

---

## 4. Social platform account requirements

Covers TikTok, YouTube, and Instagram specifically — see `docs/PROVIDER_VERIFICATION_PLAN.md` for the full sandbox/live verification steps once credentials below exist; this section is only the *prerequisite* checklist to get there.

**Per platform the pilot will publish to (repeat for each):**

- [ ] A real, dedicated creator account exists (not a personal account, not shared with unrelated content) — registered in this system via `POST /accounts` once the account itself exists on the platform.
- [ ] The account is past any platform-specific warmup/trust requirements the platform itself imposes (this system's own `warmup_status`/`account_warmup_minimum_age_days` gate is separate from, and doesn't substitute for, the platform's own new-account restrictions).
- [ ] **TikTok specifically:** a TikTok Developer app exists with the Content Posting API scope; understand *before* the pilot whether app audit (required for public, non-draft posting) has been completed or is still pending — if pending, the pilot publishes to that platform as private/draft only, or manually (§5).
- [ ] **YouTube specifically:** a Google Cloud project with the YouTube Data API v3 enabled and OAuth consent configured; confirm the default ~10,000 units/day quota is sufficient for the pilot's volume (roughly 6 uploads/day/project at default quota — almost certainly fine at 10-15 videos over 2-4 weeks, but confirm, don't assume).
- [ ] **Instagram specifically:** an Instagram Professional account linked to a Facebook Page, with a Meta app that has Content Publishing permission; confirm whether Meta App Review has been completed or the pilot will use registered test users only.
- [ ] If a platform's credentials/app-review aren't ready by pilot start: **explicitly decide** to publish to that platform manually (§5) rather than silently skip it or delay the whole pilot — a manual post to a real account with a real audience is still a real result.

---

## 5. Human review process requirements

Restates and sharpens `docs/PILOT_PLAN.md` §1.3 as a setup checklist:

- [ ] A named, specific person is assigned as pilot reviewer (or a small fixed rotation) — not "whoever's free" — and has a real operator-role JWT (via `POST /auth/token` with real `AUTH_CLIENT_ID`/`AUTH_CLIENT_SECRET`, not shared test credentials).
- [ ] That reviewer has read the pilot's actual campaign `rules_text` and brand-safety requirements *before* reviewing the first video, not while reviewing it.
- [ ] Decision made and documented: are `QUALITY_ORIGINALITY_AUTO_REJECT_FLOOR`/`QUALITY_POLICY_RISK_AUTO_REJECT_CEILING` staying at their disabled defaults (everything reaches human review) or being set (some pre-filtering)? Either is fine — an undocumented default is not.
- [ ] Reviewer agrees to supply a `reason_code` on every `rejected`/`revision_requested` decision — this is pilot data, not optional paperwork (see `docs/PILOT_PLAN.md` §4).
- [ ] A realistic reviewer time budget is agreed for the pilot's volume (10-15 videos across 2-4 weeks is not a heavy load, but "instant, unattended review" is not the goal either — genuine review takes real minutes per video).

---

## 6. Production deployment requirements

- [ ] The application is actually deployed and reachable somewhere real (not just "the Dockerfile builds in CI," which is already verified per `docs/PRODUCTION_HARDENING_REPORT.md` — that's necessary, not sufficient). `docker compose up` (or the equivalent for the chosen host) has been run in *this specific* target environment and `GET /health` returns `200` with every configured dependency reporting `ok`.
- [ ] `DATABASE_URL` points at a real, managed Postgres instance (§1's recommendation) — not a container's own ephemeral Postgres.
- [ ] A backup has actually been taken from this specific deployment and a restore has actually been tested from it (not just trusted from the H3 hardening verification, which ran in a different environment) — see `docs/DATABASE_OPERATIONS.md` §§2-3.
- [ ] Backup retention (§4 of the same doc) is configured for real, on the actual managed service or cron, not just documented.
- [ ] `GET /metrics` is being scraped by something real; `SENTRY_DSN` is set if error tracking is wanted; someone is actually watching `/health` and budget-governor alerts during the pilot window — restates `docs/PILOT_PLAN.md` §3's monitoring checklist as a hard gate, not a suggestion.
- [ ] Rollback levers are confirmed to work in this deployment specifically: `PUBLISHING_ENABLED=false` actually stops publishing without a redeploy; lowering a `BudgetCeiling` actually blocks further spend at the next check. Test both once, deliberately, before day one — don't discover during a real incident that a lever doesn't work the way the docs say.
- [ ] Media storage: confirm `MEDIA_STORAGE_DIR` is on a persistent volume in this deployment, and decide whether `MEDIA_BACKUP_ENABLED` is worth turning on for the pilot's (small) volume of real rendered assets.

---

## 7. Cost tracking requirements

Mechanically, this already works with zero setup — `agent_run()` auto-records cost to `cost_ledger` for every AI-incurring step, and `GET /videos/{id}/profit` / `GET /niches/{id}/profit` / `GET /accounts/{id}/profit` already compute rollups. What needs to be *decided*, not built:

- [ ] Set a real `BudgetCeiling` (`POST /budget/ceilings`) sized to the pilot's actual expected spend *before* producing anything — this is the fail-closed backstop, not a nice-to-have.
- [ ] Decide whether human review time gets a manual `POST /videos/{id}/cost` entry (category `human_review`) — if the pilot's profit numbers are meant to reflect fully-loaded cost, this has to be entered deliberately; the system has no way to infer a human's time cost automatically.
- [ ] Decide the cadence for entering real Whop revenue (`POST /videos/{id}/revenue`) as it settles — don't wait until the very end of the pilot to backfill all of it, since revenue timing itself is part of what `docs/PILOT_PLAN.md` §4 wants collected.
- [ ] Confirm whoever is entering cost/revenue data understands the field units and constraints (`cost_usd`/`payout_realized`/`payout_pending` are dollars, not cents; see `schemas/analytics.py`) — a unit mistake here silently corrupts every profit number downstream.

---

## 8. Security checklist

- [ ] `JWT_SECRET_KEY`, `AUTH_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, and every platform credential are real, freshly generated values — none reused from `.env.example`, test fixtures, or any other environment.
- [ ] `.env` (or the deployment's real secret store) is confirmed not committed to git and not readable by anyone outside the people who need it.
- [ ] Real platform OAuth tokens are only ever stored via `POST /accounts`'s `oauth_token` field (Fernet-encrypted at rest via `TOKEN_ENCRYPTION_KEY`) — never logged, never placed in a plaintext note field, never pasted into a chat/ticket for "debugging."
- [ ] The operator JWT used for pilot actions is scoped to the pilot (a distinct `AUTH_CLIENT_ID`, if this deployment is shared with anything else) so pilot activity is attributable and revocable independently.
- [ ] Confirm environment separation if this deployment shares infrastructure with anything else (dev/staging) — separate `DATABASE_URL`, separate secrets, per `docs/DEPLOYMENT.md` §7; a pilot is real data and real (if small) spend, and shouldn't share a database with a dev sandbox.
- [ ] `pip-audit`/`bandit` (already part of CI, per H2) are green on the exact commit being deployed for the pilot — confirm, don't assume the last green run still applies if anything changed since.

---

## 9. Which credentials are optional vs. mandatory (summary)

**Mandatory — no real pilot without these:**
- `ANTHROPIC_API_KEY` (real content generation)
- A real Postgres `DATABASE_URL`
- `JWT_SECRET_KEY` (32+ chars), `AUTH_CLIENT_ID`/`AUTH_CLIENT_SECRET` (real, pilot-specific)
- `RENDERER_BACKEND=template_pillow` or another real renderer (not `null`)
- Real Whop campaign access (a process, not a credential — §3)
- A named human reviewer with real operator access

**Mandatory only if any real platform OAuth token will be stored:**
- `TOKEN_ENCRYPTION_KEY`

**Optional, with an explicit fallback that is itself acceptable for a pilot:**
- `ELEVENLABS_API_KEY` (falls back to silent audio — acceptable if the pilot's format tolerates it)
- `TIKTOK_CLIENT_KEY`/`SECRET`, `YOUTUBE_CLIENT_ID`/`SECRET`, `INSTAGRAM_APP_ID`/`SECRET`, and each platform's OAuth token (falls back to manual publishing per platform — see §10)
- `SLACK_WEBHOOK_URL` / SMTP settings (falls back to log-only notifications)
- `SENTRY_DSN` (falls back to structured logs only)
- AWS credentials + `MEDIA_BACKUP_ENABLED` (falls back to no off-host media backup — local disk remains primary either way)
- `REDIS_URL`/`RATE_LIMIT_BACKEND=redis` (only needed if `WEB_CONCURRENCY` > 1)

## 10. Which services can remain manual during the pilot

Manual is a legitimate, real pilot path for any of these — not a degraded simulation. A human posting a real video to a real account, or entering real metrics/revenue by hand, produces the same real business result as automation would; automation is a later efficiency, not a prerequisite for the pilot's own validity:

- **Publishing** to any platform without live credentials yet — post manually, then record the outcome (external post ID, published-at time) exactly as `docs/PILOT_PLAN.md` §1.4 describes, so the data is uniform with automated platforms.
- **Metrics collection** for any platform without an automated analytics provider — enter via `POST /videos/{id}/metrics` using the same field set the automated path would populate.
- **Revenue entry** — always manual today regardless of platform (`POST /videos/{id}/revenue`); there is no automated Whop payout ingestion in this system.
- **Whop campaign discovery/submission** — always manual/semi-manual, per §3.
- **Notifications** — log-only is acceptable if a human is actually tailing the log stream during the pilot window; this only becomes a real gap if no one is watching anything at all.

## 11. Exact minimum setup to produce real, measurable results

The smallest configuration that satisfies "real, not simulated" for every number in `docs/PILOT_PLAN.md` §2:

1. Real `ANTHROPIC_API_KEY` (real research/ideas/scripts).
2. `RENDERER_BACKEND=template_pillow` (a real, playable rendered asset) — `ELEVENLABS_API_KEY` optional, silent voiceover acceptable if the format tolerates it.
3. Real Postgres `DATABASE_URL`, `ENVIRONMENT=production`, real `JWT_SECRET_KEY`/`AUTH_CLIENT_ID`/`AUTH_CLIENT_SECRET`, deployed and reachable, with a verified backup/restore.
4. A real Whop campaign identified and its terms understood (§3) — no API needed, manual entry is sufficient and expected.
5. At least one real creator account on at least one platform, published to **manually** if no live platform credentials exist yet (§10) — live publishing credentials are an optimization on top of this, not a requirement to start.
6. A named human reviewer with real operator access, committed to reviewing every pilot video with a reason code on every non-approval.
7. A real `BudgetCeiling` set before the first cost-incurring request.
8. Manual revenue entry as real Whop payout data becomes available.

Everything beyond this (live platform APIs, ElevenLabs audio, Redis/multi-worker, Sentry, S3 media backup) genuinely improves the pilot's efficiency, audio quality, or operational polish — but does not change whether its results are real. Items 1-8 are the actual gate; the rest of this checklist's optional items are worth doing when convenient, not worth delaying the pilot for.
