# Pilot Environment Setup Guide

**Purpose:** the actual, ordered, do-this-then-that workflow for turning `docs/PILOT_ENVIRONMENT_STATUS.md`'s 8 `NOT READY` rows into `READY`. `docs/PILOT_SETUP_CHECKLIST.md` explains *what's needed and why*; this document is *how to obtain it, how to configure it, how to prove it works, and exactly what evidence to record*.

**Still true:** do not execute the pilot pipeline, do not publish anything, do not enter simulated metrics/revenue. Everything in this guide is setup and verification only — every verification command below is either read-only or produces disposable, clearly-not-pilot-content test data (e.g., a throwaway campaign named `_setup_verification_delete_me`), never a real pilot artifact.

---

## 1. Step-by-step setup order

Dependencies matter — doing these out of order means re-doing work. Follow this sequence:

1. **Provision the database first** (§4.1) — everything else needs somewhere to write to.
2. **Generate and store all secrets** (§5) before setting any environment variable that holds one — never generate a secret *while* pasting it into a config file; generate it, store it in the real secret manager, then reference it.
3. **Set core environment variables and deploy the application** (§3, §4.2) — get `GET /health` returning `200` before configuring anything platform-specific.
4. **Obtain and configure the Anthropic API key** (§2.1) — verify real content generation works before spending effort on anything downstream of it.
5. **Configure the real renderer** (§2.2, no external account needed) — verify a real video file comes out the other end.
6. **Confirm the Whop campaign access process** (§2.6) — this has no technical setup, just a process to confirm; do it in parallel with the above, not last, since it can't be rushed if it turns out to need a real conversation with Whop.
7. **Assign and provision the human reviewer** (§2.7).
8. **Register at least one creator account and decide manual vs. automated publishing per platform** (§2.3-§2.5).
9. **Set the pilot's budget ceiling and agree the revenue/cost entry cadence** (§4.4).
10. **Run every verification command in §6, in order, and record evidence** (§7) — only after every earlier step is individually done, run the full sequence once more end to end to catch anything that broke between steps.
11. **Update `docs/PILOT_ENVIRONMENT_STATUS.md`** — flip each row to `READY` only as its own §7 evidence is actually in hand.

---

## 2. Required accounts and where to obtain them

### 2.1 Anthropic API key
- Obtain from the Anthropic Console (`console.anthropic.com`) — create an account/organization if one doesn't already exist for this pilot, generate an API key scoped to it.
- Note the exact model identifier you intend to use; `ANTHROPIC_MODEL`/`ANTHROPIC_MODEL_VERSION` must match what your Anthropic account actually has access to.

### 2.2 Real renderer (template_pillow)
- No external account needed — `template_pillow` uses local Pillow, already a dependency once the `rendering` extra is installed (`pip install '.[rendering]'`). This is a config/dependency decision, not a credential to obtain.
- ElevenLabs (optional, for real voiceover instead of silence): obtain an API key from ElevenLabs' own dashboard if this pilot's format needs narration.

### 2.3 TikTok (optional — only if this pilot will publish to TikTok via automation, not manually)
- TikTok Developers portal (`developers.tiktok.com`) — register an app, request the Content Posting API scope.
- Note whether app audit (required for public, non-draft posting) is complete or pending — see `docs/PROVIDER_VERIFICATION_PLAN.md` §2.

### 2.4 YouTube (optional)
- Google Cloud Console — create/select a project, enable the YouTube Data API v3, configure an OAuth consent screen, create OAuth client credentials.
- Confirm the project's daily quota (default ~10,000 units) against the pilot's expected upload volume.

### 2.5 Instagram (optional)
- Meta for Developers (`developers.facebook.com`) — create an app, add the Instagram Graph API's Content Publishing permission, link the target Instagram Professional account to a Facebook Page.
- Note whether Meta App Review is complete or the pilot will use registered test users only.

### 2.6 Whop campaign access
- Not an API credential — there is no confirmed public Whop Content Rewards API (`ARCHITECTURE.md`'s own open question). Obtain access the way it actually works today: direct contact with Whop, dashboard access to real campaign listings, or whatever the current real mechanism is. Confirm this firsthand rather than assuming a process from documentation alone.

### 2.7 Human reviewer / operator
- Not an external account — an internal assignment. Designate a specific named person (or small fixed rotation) and issue them a real, pilot-specific `AUTH_CLIENT_ID`/`AUTH_CLIENT_SECRET` pair (§3) distinct from any other environment's credentials.

### 2.8 Optional supporting services
- **Sentry** (`sentry.io`) — create a project, obtain its DSN, if error tracking is wanted.
- **Slack** — create an incoming webhook URL in the workspace that should receive budget/review alerts, if wanted over log-only notifications.
- **AWS** (only if `MEDIA_BACKUP_ENABLED` is wanted) — an IAM user/role with write access to one S3 bucket, credentials via the standard `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION` environment variables (boto3's own convention, not an app-specific setting).

---

## 3. Environment variables checklist

Set these in the real deployment target's actual secret/config mechanism (a platform's secret manager, a `.env` file that is **not** committed — see §5), not by exporting them ad hoc in a shell that will be forgotten.

| Variable | Value for the pilot | Mandatory? |
|---|---|---|
| `ENVIRONMENT` | `production` | Yes |
| `DATABASE_URL` | Real managed Postgres connection string (§4.1) | Yes |
| `JWT_SECRET_KEY` | Freshly generated, 32+ chars (§5.1) | Yes |
| `JWT_ALGORITHM` | `HS256` (default — no reason to change) | No |
| `AUTH_CLIENT_ID` / `AUTH_CLIENT_SECRET` | Freshly generated, pilot-specific (§5.1) | Yes |
| `ANTHROPIC_API_KEY` | Real key from §2.1 | Yes |
| `ANTHROPIC_MODEL` / `ANTHROPIC_MODEL_VERSION` | Match what the Anthropic account actually has access to | Yes (has defaults, but verify they're correct for your account) |
| `TTS_PROVIDER` | `elevenlabs` (if configuring real voice) or leave `silent` | No |
| `ELEVENLABS_API_KEY` | Real key from §2.2, if `TTS_PROVIDER=elevenlabs` | Only if using real TTS |
| `RENDERER_BACKEND` | `template_pillow` | Yes |
| `MEDIA_STORAGE_DIR` | A real, persistent volume path in the deployment target | Yes |
| `TOKEN_ENCRYPTION_KEY` | Freshly generated Fernet key (§5.1) | Only if storing any real platform OAuth token |
| `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` | Real values from §2.3 | Only if automating TikTok |
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` | Real values from §2.4 | Only if automating YouTube |
| `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET` | Real values from §2.5 | Only if automating Instagram |
| `PUBLISHING_ENABLED` | `true`, with the rollback lever tested (§6.8) | Yes |
| `NOTIFICATION_PROVIDER` | `slack` or `email`, if configured (§2.8); otherwise leave `log` | No |
| `SLACK_WEBHOOK_URL` | Real webhook from §2.8 | Only if `NOTIFICATION_PROVIDER=slack` |
| `SENTRY_DSN` | Real DSN from §2.8 | No, but recommended |
| `METRICS_ENABLED` | `true` (default) | No |
| `MEDIA_BACKUP_ENABLED` / `MEDIA_BACKUP_S3_BUCKET` | `true` / real bucket, if configured (§2.8) | No |
| `WEB_CONCURRENCY` | `1`, unless `RATE_LIMIT_BACKEND=redis` is also set up | No |
| `LOG_LEVEL` | `INFO` (default) | No |

---

## 4. Production deployment checklist

### 4.1 Database
1. Provision a real managed Postgres instance (RDS, Cloud SQL, Supabase, or equivalent — `docs/DATABASE_OPERATIONS.md` §1's recommendation). Enable automated backups and point-in-time recovery at provisioning time — this is a checkbox on every major managed service, not a separate task.
2. Record the connection string as `DATABASE_URL`.
3. From a machine that can reach it: `alembic upgrade head` (or the `docker-entrypoint.sh migrate` path, §4.2) to create the schema.
4. Take a real backup and perform a real restore drill against this specific instance (§6.2) — do this before any pilot data exists in it, both because it's a good time to test and because it establishes the very first restore point.

### 4.2 Application deployment
1. Build the container image from this repository's `Dockerfile` (already verified to build successfully in CI — see `docs/PRODUCTION_HARDENING_REPORT.md`).
2. Run the `migrate` variant once, to completion, *before* starting any `serve` replica: `docker run --rm <image> migrate` (or `docker compose run --rm migrate` if using the provided compose file) — this runs `alembic upgrade head` and exits; it must never run implicitly as a side effect of `serve` starting (see `docker-entrypoint.sh`'s own comment on why).
3. Start the `serve` replica(s): `docker run <image>` (default `CMD` is `serve`) or `docker compose up app`.
4. Confirm `GET /health` returns `200` with every configured dependency `ok` (§6.1) before proceeding to any provider-specific setup.

### 4.3 Rollback levers — test once, deliberately, now
1. Set `PUBLISHING_ENABLED=false`, confirm `POST /videos/{id}/publish` now returns `503` (no redeploy required — the setting is read per-request via `get_settings()`), then set it back to `true`.
2. Create a throwaway `BudgetCeiling` at a tiny limit, confirm a cost-incurring request now returns `402`, then either raise the ceiling or delete the throwaway data.

### 4.4 Budget and revenue-cadence setup
1. `POST /budget/ceilings` with the pilot's real, agreed spend limit (system-wide, or scoped to the pilot's niche).
2. Decide and document, in `docs/PILOT_ENVIRONMENT_STATUS.md`'s notes for item 8: will reviewer time be entered via `POST /videos/{id}/cost` (category `human_review`)? On what cadence will real Whop revenue be entered via `POST /videos/{id}/revenue`?

---

## 5. Security precautions for secrets

1. **Generate every secret fresh, specifically for this pilot** — never reuse a value from `.env.example`, this repository's test fixtures (`test-operator`/`test-operator-secret`, `test-jwt-secret-do-not-use-in-production`), or any other environment.
   - `JWT_SECRET_KEY`: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
   - `TOKEN_ENCRYPTION_KEY`: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - `AUTH_CLIENT_SECRET`: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
2. **Store secrets in the deployment target's real secret manager** (a cloud provider's secret store, a platform's encrypted env-var feature) — a `.env` file is acceptable only if the deployment target has no better mechanism, and even then it must never be committed (confirm `.gitignore` covers it, which this repository's already does) and must be readable only by the processes/people that need it.
3. **Never paste a real secret into a chat, ticket, log line, or this repository** — if a secret is ever accidentally exposed this way, rotate it immediately; treat "was it actually used maliciously" as unknowable and act on exposure alone.
4. **Scope credentials narrowly** — the Anthropic key, the database user, and each platform's app credentials should have only the access this pilot actually needs, not broad account-wide access "to be safe later."
5. **Real platform OAuth tokens are stored only via `POST /accounts`'s `oauth_token` field**, which encrypts at rest via `TOKEN_ENCRYPTION_KEY` (Fernet) and is never serialized back out (`AccountOut` exposes only `has_credentials: bool`) — never store a token anywhere else (a note field, a spreadsheet, a ticket) even temporarily.
6. **Confirm environment separation** — if this deployment shares any infrastructure with development/CI, every secret above must still be a distinct value from that environment's, per `docs/DEPLOYMENT.md` §7.
7. **Re-run `bandit`/`pip-audit`** (`bandit -r src/content_factory -ll`, `pip-audit -r requirements-lock.txt`) against the exact commit being deployed, immediately before deploying it — confirm the CI result still applies to what's actually shipping, don't assume.

---

## 6. Verification commands for each dependency

Run these against the real deployed pilot environment (not this development sandbox). Each command's expected output is the evidence recorded in §7.

**6.1 Application health**
```
curl -s https://<pilot-host>/health
```
Expect `{"status": "ok", "checks": {"database": "ok", ...}}` with HTTP `200`. Any `"unreachable"` value or `503` means stop and fix that dependency before proceeding.

**6.2 Database backup/restore**
```
DATABASE_URL=<pilot DATABASE_URL> ./scripts/backup_postgres.sh
DATABASE_URL=<a fresh throwaway database URL> ./scripts/restore_postgres.sh /path/to/the/produced/.dump
psql "<the throwaway database URL>" -c "select count(*) from alembic_version;"
```
Expect the backup script to complete and print an archive path; the restore script to complete without the `FORCE=1` guard triggering (a fresh database has zero tables); and the final `psql` check to return `1` row. Then drop the throwaway database.

**6.3 Anthropic key / real content generation**
```
curl -s -X POST https://<pilot-host>/auth/token -H "Content-Type: application/json" \
  -d '{"client_id": "<real AUTH_CLIENT_ID>", "client_secret": "<real AUTH_CLIENT_SECRET>"}'
# use the returned access_token as a Bearer token below
curl -s -X POST https://<pilot-host>/campaigns \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"brand_name": "_setup_verification_delete_me", "cpm_rate": 1.0}'
curl -s -X POST https://<pilot-host>/campaigns/<id>/research \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"raw_notes": "verification only, not a real campaign"}'
```
Expect the research response's `structured_data` to contain genuine, non-empty, non-placeholder generated text (a real `brief_text`, real `competitor_hooks`) — if it looks like the canned/empty fallback shape, `ANTHROPIC_API_KEY` isn't actually being used; check `resolved_llm_provider()`'s fallback condition. Delete this throwaway campaign afterward (or leave it clearly named as verification-only and exclude it from pilot data).

**6.4 Real renderer**
```
curl -s -X POST https://<pilot-host>/campaigns/<id>/ideas \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"concept_summary": "verification only"}'
curl -s -X POST https://<pilot-host>/ideas/<id>/scripts \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"num_variants": 1}'
curl -s -X POST https://<pilot-host>/scripts/<id>/render \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{}'
```
Expect the resulting `Video.asset_url` to point at a real, playable video file (open it) with `render_status: "completed"` — not a JSON manifest with no actual media. Confirm the file exists on `MEDIA_STORAGE_DIR`'s real, persistent volume and survives a container restart.

**6.5 Whop campaign access**
No command — this is a firsthand confirmation, not a system check. Evidence is a written note of how access actually works (who has it, how briefs/terms are obtained, how submission/earnings are reported back) plus the names of at least 2-3 real, currently-open campaigns identified.

**6.6 Human reviewer access**
```
curl -s -X POST https://<pilot-host>/auth/token -H "Content-Type: application/json" \
  -d '{"client_id": "<reviewer AUTH_CLIENT_ID>", "client_secret": "<reviewer AUTH_CLIENT_SECRET>"}'
curl -s -X POST https://<pilot-host>/videos/<verification-video-id>/review \
  -H "Authorization: Bearer <reviewer token>" -H "Content-Type: application/json" \
  -d '{"decision": "approved"}'
```
Expect a successful token issuance and a `200` on the review call, with the resulting `ReviewDecision.reviewer_id` matching the reviewer's actual identity (the JWT subject, not a client-supplied name) — confirms the specific named reviewer's credentials actually work end to end.

**6.7 Publishing (per platform, or manual)**
- Automated: `POST /videos/{id}/publish` with a real `account_id`; expect a real `external_post_id` back and the post actually visible on the platform.
- Manual: post the verification video manually to the real account per `docs/PROVIDER_VERIFICATION_PLAN.md`'s sandbox steps, then record the outcome the same way automation would.
Either way, confirm `GET /publications` shows the resulting row.

**6.8 Budget ceiling and rollback**
```
curl -s -X POST https://<pilot-host>/budget/ceilings -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" -d '{"scope": "system", "monthly_limit_usd": 0.01}'
# then attempt any cost-incurring call, e.g. the research call from 6.3 again
```
Expect a `402` once the tiny ceiling is exceeded — confirms the fail-closed governor is real and active, not just configured. Then set the ceiling to the pilot's actual real value.

**6.9 Metrics/monitoring**
```
curl -s https://<pilot-host>/metrics | head -5
```
Expect real Prometheus-format output (not a 404) if `METRICS_ENABLED=true` and the `observability` extra is installed. Confirm a real scraper is actually configured to pull this endpoint, not just that it responds.

---

## 7. Exact criteria to move each `PILOT_ENVIRONMENT_STATUS.md` row from NOT READY to READY

For every item: **READY requires all three** — configuration done, verification run, evidence recorded (a timestamp, the command output or a link to it, and who ran it) in `docs/PILOT_ENVIRONMENT_STATUS.md`'s `Notes` column for that row.

**1. Anthropic API key**
- *Configured:* `ANTHROPIC_API_KEY` set in the real deployment's secret store; `ANTHROPIC_MODEL`/`ANTHROPIC_MODEL_VERSION` confirmed to match account access.
- *Tested:* §6.3.
- *Evidence for READY:* the research call's actual response body (or a clear excerpt showing genuine generated text, not the empty/canned fallback shape), with a timestamp and who ran it.

**2. Production Postgres environment**
- *Configured:* real managed instance provisioned, automated backups + PITR enabled at the provider level, `DATABASE_URL` set, schema migrated to head.
- *Tested:* §6.2 (backup + restore drill) and §6.1 (health check's `database: ok`).
- *Evidence for READY:* the backup archive's filename/timestamp, the restore drill's row-count confirmation output, and a `curl /health` response showing `database: ok`.

**3. Production environment variables**
- *Configured:* every "Yes"-mandatory row in §3 set to a real, freshly-generated (§5.1), pilot-specific value.
- *Tested:* `ENVIRONMENT=production` boot-time check passing (the app actually started — if `JWT_SECRET_KEY`/`DATABASE_URL` were wrong, `Settings.validate_production_safety()` would have refused to start, so a successful health check in §6.1 is itself partial evidence); §6.1's health check; §6.6's real-reviewer-token issuance (confirms `AUTH_CLIENT_ID`/`SECRET` are real and working).
- *Evidence for READY:* confirmation the app is running under `ENVIRONMENT=production` (e.g., the deployment platform's own config view), plus the §6.1 and §6.6 outputs.

**4. Real renderer configuration**
- *Configured:* `RENDERER_BACKEND=template_pillow` (or another real renderer) set; the `rendering` extra installed in the deployed image.
- *Tested:* §6.4.
- *Evidence for READY:* a link to or copy of the actual rendered video file produced by the verification script, confirmed playable, plus the API response showing `render_status: "completed"`.

**5. Whop campaign access process**
- *Configured:* N/A (a process, not a system) — the "configuration" is the confirmed access method itself.
- *Tested:* §6.5 (firsthand confirmation, not a system command).
- *Evidence for READY:* a written note naming how access works, who has it, and the 2-3 real campaigns identified by name, with a timestamp and who confirmed it.

**6. Human reviewer/operator access**
- *Configured:* a named person assigned; real, pilot-specific `AUTH_CLIENT_ID`/`AUTH_CLIENT_SECRET` issued to them.
- *Tested:* §6.6.
- *Evidence for READY:* the §6.6 output showing a successful token issuance and review submission under that reviewer's real identity, plus the reviewer's name recorded as `Owner` in the status document.

**7. Publishing workflow/accounts**
- *Configured:* at least one real creator account registered (`POST /accounts`); an explicit manual-vs-automated decision made and documented per platform the pilot will use.
- *Tested:* §6.7.
- *Evidence for READY:* the resulting `Publication` row (or the manual-post confirmation) plus the documented per-platform decision.

**8. Metrics and revenue tracking setup**
- *Configured:* a real `BudgetCeiling` set to the pilot's agreed value; the cost-entry and revenue-entry cadence decided and documented.
- *Tested:* §6.8, and confirmation `GET /metrics` responds if monitoring is configured (§6.9).
- *Evidence for READY:* the §6.8 output showing the fail-closed `402` at a test ceiling, the ceiling reset to its real pilot value, and the written cost/revenue cadence decision.

---

**The pilot remains blocked** — per `docs/PILOT_ENVIRONMENT_STATUS.md`'s own Pilot Ready Criteria — until every mandatory row above has all three: `Status: READY`, a named `Owner`, and the specific evidence described in this section recorded in that document's `Notes` column. Do not flip a row to `READY` on the basis of configuration alone; the verification and evidence steps are what "ready" actually means here.
