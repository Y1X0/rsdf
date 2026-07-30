# Phase 1 & 2 Implementation — Developer Guide

This started as the Phase 1 MVP described in `ARCHITECTURE.md` §16/§22-23:
manual campaign input, a working Research Agent and Script Agent, a
template-based production pipeline, a human review workflow, and an
analytics/cost/revenue foundation, exposed over a REST API. **Phase 2**
(below) closes the seven capability gaps §16's "Partial automation" column
calls for: real platform publishing, metrics ingestion, an active Cost
Control Layer, Creator Account Management, quality-gate thresholds, and the
Experimentation Engine (recommend-only) plus Revenue Optimization rollups.
Generative video and fully-autonomous publishing remain out of scope until
Phase 3.

**Version 1.1** is a stability & security patch release — see
`docs/PHASE1_AUDIT.md` for the findings it addresses and
`docs/PHASE1_AUDIT_v2.md` for the re-audit and Go/No-Go verdict that
unlocked Phase 2. No new product features were added in v1.1; every change
there was a fix.

## What's implemented

| Goal | Where |
|---|---|
| 1. Campaign workflow (manual input, storage, scoring) | `db/models/campaign.py`, `services/campaign_scoring.py`, `api/routers/campaigns.py`, `api/routers/niches.py` |
| 2. Research Agent | `agents/research_agent.py` |
| 3. Script Agent | `agents/script_agent.py` |
| 4. Content Intelligence (hook_library, patterns, outcome tracking) | `db/models/hook.py`, `services/content_intelligence.py` |
| 5. Production pipeline (template-based) | `services/production_service.py`, `services/qc_service.py`, `video_production/` |
| 6. Human Review workflow | `services/review_service.py`, `api/routers/review.py` |
| 7. Analytics foundation (views/retention/engagement/revenue/cost) | `services/analytics_service.py` |
| 8. Dashboard/API | `api/routers/dashboard.py` + FastAPI's built-in `/docs` |

## v1.1 changes at a glance

| Audit finding | Fix | Where |
|---|---|---|
| F1 (critical) — a later pipeline step's failure rolled back an earlier step's already-completed, already-billed `AgentRun`/`CostLedger` records | `agent_run()` and `run_idempotent()` now `commit()` at each COMPLETED/FAILED transition instead of only `flush()` | `agents/base.py`, `services/idempotency.py` |
| F1's corollary — the idempotency "retry after failure" branch was reachable only in bare unit tests, not the real API | Same fix as above — a FAILED record now survives the request-level rollback | `services/idempotency.py` |
| F2 (critical) — no authentication or authorization anywhere | JWT bearer auth (`require_auth`/`require_operator`), `POST /auth/token` issuance, review endpoint now uses the authenticated identity instead of a client-supplied `reviewer_id` | `auth/`, `api/routers/auth.py`, all routers |
| F3 (high) — niche saturation/trend/CPM had no write path; scoring was permanently neutral | `api/routers/niches.py` CRUD; scoring derives a real internal signal (campaign count, hook performance) before falling back to neutral | `api/routers/niches.py`, `services/campaign_scoring.py` |
| F4 (high) — `qc_status` was hardcoded `"passed"` | Real automated checks (audio present, captions cover audio, duration plausible, asset exists) | `services/qc_service.py` |
| F5 (high) — two check-then-act races with no DB backstop | Shared safe-upsert helper (SAVEPOINT + fallback to the concurrent winner); new unique constraint on `hook_library(niche_id, hook_text)` | `services/db_safety.py`, migration `0002` |
| F6 (high) — two missing indexes on columns in live query paths | Indexes added on `videos.status` and `campaigns.niche_id` | migration `0002` |
| F7 (high) — no bounds on cost-sensitive fields (`num_variants` especially) | `Field` bounds (`ge`/`le`/`max_length`) across every request schema | `schemas/*.py` |
| F11 (medium) — two endpoints returned untyped dicts | `CostEntryOut`/`RevenueEntryOut` response models | `schemas/analytics.py` |
| F20 (medium) — `/health` didn't check anything | Checks real DB connectivity via its own short-lived connection | `api/main.py` |
| (new in this pass) no generic exception handler | Unhandled exceptions now log with context and return a proper 500 instead of crashing the process view | `api/main.py` |

## Phase 2 (M1-M6)

Delivered milestone by milestone, one commit and one Alembic migration per
milestone (`0003`-`0007`), full suite green after each before the next
started — see the approved Phase 2 implementation plan for the full
rationale behind each design choice. Every addition is additive: no
existing Phase 1/v1.1 table, endpoint contract, or service signature
changed, and all 115 Phase 1/v1.1 tests still pass unmodified.

| Milestone | What it adds | Where |
|---|---|---|
| M1 — Cost Control Layer | Active budget governor (`check_budget`/`enforce_budget`), computed on demand from `cost_ledger` — never a cached counter; fires an alert exactly once per 50/80/95/100% threshold via a new `NotificationProvider` interface (log/Slack/email); fails closed (402) at 100% until a human raises the ceiling. Also closes audit item N1: fixed-window rate limiting on `POST /auth/token`. | `services/budget_governor.py`, `notifications/`, `auth/rate_limiter.py`, migration `0003` |
| M2 — Quality Scoring threshold gating | Opt-in (disabled by default) auto-reject: a video whose `originality_score`/`policy_risk_score` breaches a configured threshold goes straight to `REJECTED` with a system-authored `ReviewDecision`, reusing the existing review/audit machinery. Also adds `niches.allocation_weight` ahead of M6. | `services/quality_scoring.py::determine_auto_reject_reason`, migration `0004` |
| M3 — Creator Account Management | `owned_accounts`/`account_health_snapshots`; heuristic health scoring (cadence-vs-cap, engagement trend, strikes, API error rate) mapped to Healthy/Watch/At-Risk/Restricted tiers; Fernet-encrypted OAuth tokens (never serialized as more than `has_credentials: bool`); warmup graduation (`warming` → `active`) gated on account age + health tier. | `services/account_service.py`, `services/token_encryption.py`, `api/routers/accounts.py`, migration `0005` |
| M4 — Publishing Agent | New `publishing/` package (mirrors `video_production/`'s shape): `PublishingProvider` interface, `ManualPublishingProvider` default (always available — readies content for a human to post), real TikTok/YouTube/Instagram providers (structurally complete, never exercised live, unit-tested against mocked HTTP). Enforces account-health-tier and daily-cadence-cap guardrails in code before any provider call; retry-with-backoff (closes audit item F19) on the first real external HTTP integration; `PUBLISHING_ENABLED` kill-switch. | `publishing/`, `services/publishing_service.py`, `api/routers/publications.py`, migration `0006` |
| M5 — Metrics Ingestion Automation | New `analytics_ingestion/` package, same shape as `publishing/`. `POST /publications/{id}/metrics/sync` feeds a fetched result through the *existing, unchanged* `analytics_service.record_metrics` — the manual `POST /videos/{id}/metrics` endpoint is untouched and remains the permanent fallback, not a transitional path. | `analytics_ingestion/`, `api/routers/publications.py` |
| M6 — Experimentation Engine + Revenue rollups | All four §5 axes (hook, niche, length, posting_time) as a documented heuristic (candidate must beat the mean of all other eligible subjects by a configurable margin — not a real significance test). Strictly recommend-only: `POST /experimentation/run` only ever writes `experiment_results`; only the separate `POST /experimentation/recommendations/{id}/apply` mutates `niches.allocation_weight` or `learning_patterns.confidence_tier`. Plus `GET /niches/{id}/profit` and `GET /accounts/{id}/profit`, reusing `compute_profit_summary`'s aggregation pattern. | `services/experimentation_service.py`, `api/routers/experimentation.py`, migration `0007` |

**New Settings this phase** (all documented in `.env.example`, all empty/
safe-default unless explicitly configured): `AUTH_TOKEN_RATE_LIMIT_*`,
`NOTIFICATION_PROVIDER`/`SLACK_WEBHOOK_URL`/`SMTP_*`,
`QUALITY_ORIGINALITY_AUTO_REJECT_FLOOR`/`QUALITY_POLICY_RISK_AUTO_REJECT_CEILING`,
`TOKEN_ENCRYPTION_KEY`/`ACCOUNT_WARMUP_MINIMUM_AGE_DAYS`,
`PUBLISHING_ENABLED`/`TIKTOK_*`/`YOUTUBE_*`/`INSTAGRAM_*`.

**New optional extras:** `notifications` and `publishing` (both just
`httpx`, mirroring the existing `elevenlabs` extra) — real Slack/platform
providers fall back to their zero-dependency default the same way
`resolved_tts_provider()` already does when no API key is configured.

## Setup

Requires Python 3.11+ and a Postgres instance (SQLite also works for local
dev/tests — see "Database portability" below).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# edit .env: set DATABASE_URL, JWT_SECRET_KEY, AUTH_CLIENT_ID/AUTH_CLIENT_SECRET,
# and any real provider API keys you have. Every provider key is optional —
# see "Running without any API keys" below. JWT_SECRET_KEY is NOT optional
# for real use — every authenticated route fails closed (500) without it.

alembic upgrade head
uvicorn content_factory.api.main:app --reload
```

Then open `http://localhost:8000/docs` for interactive API docs — this
doubles as the Phase 1 "dashboard" per `ARCHITECTURE.md` §22 ("even a
spreadsheet-backed one is fine"); a real Next.js dashboard is Phase 2+. You
still need a bearer token to call anything from `/docs`'s "Try it out" —
use the "Authorize" button with a token from `POST /auth/token` (see
"Authentication" below).

## Authentication

Every business route requires a JWT bearer token (`Authorization: Bearer
<token>`); only `GET /health` and `POST /auth/token` are open. Phase 1 has
no user database — a small, fixed set of pre-shared client credentials
(configured via `AUTH_CLIENT_ID`/`AUTH_CLIENT_SECRET`) is issued short-lived
tokens, the standard client-credentials pattern for service-to-service
auth. Get a token:

```bash
curl -X POST localhost:8000/auth/token -H 'content-type: application/json' -d '{
  "client_id": "'"$AUTH_CLIENT_ID"'", "client_secret": "'"$AUTH_CLIENT_SECRET"'"
}'
# => {"access_token": "...", "token_type": "bearer", "expires_in": 3600}
```

Then pass it on every subsequent request: `-H "Authorization: Bearer $TOKEN"`.

Two authorization tiers, both required to be present in the token's `role`
claim: `require_auth` (any valid token — used on every `GET`) and
`require_operator` (used on every mutating route). Phase 1 issues only
`operator` tokens; a read-only role is the seam Phase 2 would add without
touching every route again.

`POST /videos/{id}/review` derives the persisted reviewer identity from the
token's `sub` claim, not from the request body — a legacy `reviewer_id`
field is still accepted for backward compatibility but is ignored (and
logged) if it disagrees with the authenticated identity.

**If `JWT_SECRET_KEY` is unset**, every authenticated route returns `500,
"Authentication is not configured on this server"` — fails closed, never
silently open.

## Running without any API keys

Every external *provider* integration is optional by design (adjustment #6
+ the `resolved_*_provider()` methods in `config.py`) — this is separate
from the authentication above, which is always required:

- No `ANTHROPIC_API_KEY` (or, if `LLM_PROVIDER=groq`, no `GROQ_API_KEY`) →
  the Research/Script Agents use `FakeLLMClient`, which returns an empty
  JSON array/object instead of crashing. You'll see `llm_provider_fallback`
  in the logs. Real generation requires a real key. `LLM_PROVIDER=groq` is
  a free-tier alternative to Anthropic (`llm/providers/groq_provider.py`) —
  same `LLMClient` interface, selected the same way, added when a paid
  Anthropic account wasn't available for the pilot's first run.
- `TTS_PROVIDER=silent` (default) → produces a real (silent) WAV file using
  only the Python standard library — no ElevenLabs key needed.
- `RENDERER_BACKEND=null` (default) → produces a JSON render manifest
  instead of an actual video — no ffmpeg/Pillow needed. This is also what
  the test suite always uses.

This means the entire pipeline — campaign → research → script → render →
review → metrics → profit — is exercisable end to end with zero provider
secrets (just the JWT auth config), which is also exactly how the test
suite works.

To use real providers: set `ANTHROPIC_API_KEY` (or `LLM_PROVIDER=groq` +
`GROQ_API_KEY`, installing with `pip install '.[groq]'`), and/or
`TTS_PROVIDER=elevenlabs` + `ELEVENLABS_API_KEY` (install with
`pip install '.[elevenlabs]'`), and/or `RENDERER_BACKEND=template_pillow`
(install with `pip install '.[rendering]'`).

## Database portability (Postgres in prod, SQLite in tests)

Every model avoids Postgres-only column features (no native `ENUM` types —
`Enum(..., native_enum=False)` is used everywhere; no `ARRAY` columns — JSON
lists are used instead). This means the exact same `Base.metadata` and the
same Alembic migrations produce a working schema on both Postgres
(production/dev — matches `ARCHITECTURE.md` §14) and in-memory SQLite (the
whole test suite, see `tests/conftest.py`). Verified: `alembic check`
reports no drift against a real local Postgres 16 instance for every
migration `0001`-`0007`, including a full `base` → `head` → `base` → `head`
round-trip, not just each new migration in isolation.

## Running tests

```bash
pytest
```

210 tests (115 Phase 1/v1.1 + 95 Phase 2), all against in-memory SQLite
with fake/silent/null/manual providers —
no network access, no database server, no real API keys required (a fixed
test JWT secret is configured in `tests/conftest.py`, since auth itself is
always on). Structure:

- `tests/unit/` — one module per service/agent, covering the score formulas,
  idempotency edge cases (including the concurrent-insert race and the
  rollback-survival regression), agent failure handling, QC checks, and
  provider behavior in isolation.
- `tests/api/` — full HTTP request/response tests through FastAPI's
  `TestClient`, including a complete pipeline walkthrough
  (`test_full_pipeline_idea_to_script_to_render_to_review`), the auth
  suite (`test_auth_api.py`), the durability regression suite
  (`test_durability_regression.py`), and request-validation coverage
  (`test_validation.py`).

The `client` fixture auto-attaches a valid operator token fetched from the
real `/auth/token` endpoint, so every test written before v1.1's auth
requirement kept passing unchanged; `unauthenticated_client` is the
dedicated fixture for the negative-path auth tests.

## A full pipeline walkthrough (curl)

```bash
TOKEN=$(curl -s -X POST localhost:8000/auth/token -H 'content-type: application/json' -d '{
  "client_id": "'"$AUTH_CLIENT_ID"'", "client_secret": "'"$AUTH_CLIENT_SECRET"'"
}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
AUTH="-H \"Authorization: Bearer $TOKEN\""

# 0. Niche management (v1.1, feeds Campaign Intelligence scoring)
curl -X POST localhost:8000/niches -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{
  "name": "personal_finance", "saturation_score": 0.3, "trend_score": 0.7
}'

# 1. Campaign workflow (goal #1)
curl -X POST localhost:8000/campaigns -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{
  "brand_name": "Acme Corp", "niche_name": "personal_finance",
  "cpm_rate": 4.0, "budget_cap": 5000, "rules_text": "Must disclose sponsorship."
}'
curl -X POST localhost:8000/campaigns/1/score -H "Authorization: Bearer $TOKEN"

# 2. Research Agent (goal #2)
curl -X POST localhost:8000/campaigns/1/research -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"raw_notes": "competitors are using countdown-style hooks this week"}'

# 3. Script Agent (goal #3) — retrieves hooks from Content Intelligence (goal #4)
curl -X POST localhost:8000/campaigns/1/ideas -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"concept_summary": "3 budgeting myths that keep people broke"}'
curl -X POST localhost:8000/ideas/1/scripts -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{"num_variants": 3}'

# 4. Production pipeline (goal #5) — TTS -> captions -> template renderer -> real automated QC
curl -X POST localhost:8000/scripts/1/render -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{}'

# 5. Human review (goal #6) — reviewer identity comes from the token, not the body
curl localhost:8000/videos/pending-review -H "Authorization: Bearer $TOKEN"
curl -X POST localhost:8000/videos/1/review -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"decision": "approved"}'

# 6. Analytics foundation (goal #7)
curl -X POST localhost:8000/videos/1/metrics -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{
  "views": 12000, "avg_watch_time_s": 22, "completion_rate": 0.55,
  "shares": 30, "comments": 60, "likes": 800
}'
curl -X POST localhost:8000/videos/1/revenue -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"campaign_id": 1, "approved_views": 8000, "payout_realized": 32.0, "status": "paid"}'
curl localhost:8000/videos/1/profit -H "Authorization: Bearer $TOKEN"

# 7. Dashboard (goal #8)
curl localhost:8000/dashboard/summary -H "Authorization: Bearer $TOKEN"
```

## Major technical decisions

Each of these expands on a line item from the approved implementation plan
or the v1.1 patch scope.

**Modular monolith, not microservices.** `ARCHITECTURE.md` describes an
event-driven, Temporal-orchestrated system for scale. Phase 1's volume
(15-20 videos/month, a human reviewing every one) doesn't need durable
workflow execution or a message queue — it needs correct, testable
synchronous request/response code. The module boundaries (`agents/`,
`services/`, `video_production/`, `api/`, `auth/`) mirror where Phase 2/3
service boundaries would go, so extraction later is a refactor, not a
rewrite.

**Durability over strict per-request atomicity (v1.1, F1).** The original
design committed once, at the very end of a request, which gave clean
atomicity but the wrong failure mode: a later step's failure erased an
earlier step's already-real, already-billed AgentRun/CostLedger rows.
`agent_run()` and `run_idempotent()` now commit at each COMPLETED/FAILED
transition. This deliberately trades strict "no partial data" atomicity for
"durable as of each completed step" — the right tradeoff once a step can
represent money already spent, not just data already written.

**Every AI output is versioned through one table, `agent_runs`.** Rather
than bolting versioning fields onto every entity that happens to involve an
LLM/TTS/render call, `agents/base.py`'s `agent_run()` context manager is the
single code path that can write an `AgentRun` row, and it requires
prompt/model/model_version/cost/duration as part of its API — there's no
way to record a partial entry. It also now automatically derives that
run's `CostLedger` entry (passing `cost_video_id`/`cost_campaign_id`) —
previously this was a manual follow-up call each caller had to remember,
and the Research/Script agents' LLM costs were silently never ledgered at
all. This is also the audit log (`ARCHITECTURE.md` §12/§17.4): append-only,
and marked `FAILED` with the error message rather than silently dropped
when a provider call raises.

**Idempotency is one reusable mechanism, not three.** `services/idempotency.py`
implements a single `(scope, key) -> fingerprint` pattern used identically
by campaign creation, research triggering, script generation, and
rendering — all four are "the same request should never redo paid work."
When no explicit key is supplied, the request payload's own fingerprint is
used as the key, so accidental duplicate submissions are caught even
without the caller thinking to pass one. Creating a new record is now a
safe upsert (`_get_or_create_record`, mirroring `services/db_safety.py`) —
a concurrent duplicate request falls back to the winner's row instead of
crashing on the unique constraint.

**JWT auth via pre-shared client credentials, not a user database (v1.1,
F2).** Phase 1 has exactly one real operator today; inventing a full user/
password system would be scope creep for a stability patch. `POST
/auth/token` issues short-lived HS256 JWTs to a small, env-configured set of
client identities — the standard OAuth2 client-credentials shape. Every
business route requires a valid token (`require_auth`); mutating routes
additionally require an `operator` role claim (`require_operator`). A real
per-user store with more granular roles is Phase 2+ territory once there's
more than one principal to distinguish.

**External providers are always behind an interface, chosen once.**
`llm/factory.py`, `video_production/tts/factory.py`, and
`video_production/renderer/factory.py` are the only places that import a
concrete provider (`anthropic`, `elevenlabs`/`httpx`, `Pillow`/`imageio_ffmpeg`)
based on configuration; every agent and service only ever depends on the
corresponding `LLMClient` / `TTSProvider` / `VideoRenderer` interface type,
injected via FastAPI's dependency system (`api/deps.py`). Swapping in
Remotion, Runway, Kling, or a different LLM later means adding one provider
class and one `if` branch in a factory — nothing else changes.

**The renderer defaults to a manifest, not pixels.** `NullRenderer` is the
default `RENDERER_BACKEND` specifically so the system runs (and is fully
tested) without ffmpeg/Pillow installed anywhere. `TemplatePillowRenderer`
is a real, working reference implementation of the same interface, gated
behind the `rendering` extra — it demonstrates the abstraction is genuinely
pluggable rather than a theoretical interface with one hidden hard
dependency.

**Automated QC is now real checks, not a hardcoded string (v1.1, F4).**
`services/qc_service.py` verifies the audio file actually exists and is
non-empty, captions actually cover the audio's duration, the rendered
duration is plausible relative to the script's target, and the render
asset exists on disk (skipped for remote-URL assets). This is deliberately
structural verification — "did the pipeline do what it claimed" — not a
perceptual quality model; that's Phase 2+ territory
(`retention_prediction_score`).

**Campaign Intelligence derives real signals before falling back to
neutral (v1.1, F3).** `services/campaign_scoring.py`'s
`compute_competition_level`/`compute_niche_fit_score` used to go straight
to a hardcoded 0.5 the moment a niche's manual fields were unset — and
there was no endpoint to ever set them. `api/routers/niches.py` closes the
write-path gap; absent a manual value, scoring now derives a real signal
from internal data (how many other campaigns already compete for this
niche, how the niche's own hooks have actually performed) before falling
back to the neutral default — and `breakdown_json` always records which of
the three produced each number.

**Status + timestamp fields everywhere, for a queue that doesn't exist
yet.** `ResearchBrief`, `Script`, and `Video` all carry a `ProcessingStatus`
plus request/completion timestamps, even though Phase 1 executes everything
synchronously in the request/response cycle. A Phase 2 background worker
can pick up `WHERE status = 'pending'` rows with no migration required.

**Viral score normalization is a documented simplification.**
`ARCHITECTURE.md` §2.5 calls for per-niche/account trailing-window z-score
baselines, which need more published-video history than Phase 1 will have.
`services/analytics_service.py` instead bounds each raw metric against a
fixed reference ceiling (`REFERENCE_WATCH_TIME_S` etc.) — clearly named
constants, not a hidden guess, and the obvious place a real baseline model
plugs in later.

**Quality scoring is heuristic, not learned, per the approved scope.**
`services/quality_scoring.py` computes originality (Jaccard word-overlap
against prior scripts/hooks in the niche) and policy risk (keyword scan)
for real; `retention_prediction_score` and `monetization_probability_score`
are left `null` on purpose — the column exists so a Phase 2 model can
populate it without a schema change, and API consumers must treat `null` as
"not available yet," not zero. (Note: this is distinct from `qc_status` —
see the QC entry above — quality scoring judges the *content*, QC judges
whether the *pipeline* did its job.)

## Known limitations (intentional, not bugs)

Resolved by Phase 2 (kept here, struck through, for history — see the
Phase 2 table above for what replaced each):

- ~~No real platform publishing (TikTok/YouTube/IG)~~ — M4's `publishing/`
  package adds real (if never-live-exercised) providers behind
  `PublishingProvider`, plus the always-available `ManualPublishingProvider`
  fallback.
- ~~Metrics/cost/revenue are entered manually via API — no platform
  Analytics API polling yet~~ — M5's `analytics_ingestion/` package adds
  `POST /publications/{id}/metrics/sync`; manual entry remains available
  and untouched.
- ~~Account health/warmup (`ARCHITECTURE.md` §8) is not built~~ — M3 adds
  `owned_accounts`/`account_health_snapshots`, health scoring, and warmup
  graduation.

Still true after Phase 2:

- Whop campaign discovery is fully manual (`POST /campaigns`) — no scraping,
  per `ARCHITECTURE.md` §0's caveat about the lack of a confirmed public API.
- Vector/embedding-based hook retrieval is deferred; `content_intelligence.get_top_hooks`
  uses plain SQL filter+sort. Swapping in pgvector similarity search later
  only touches that one function.
- Auth is single-tier client-credentials (one shared identity, one
  `operator` role) — no per-user accounts, no read-only role in practice
  yet, no token refresh/revocation flow beyond natural expiry.
- No CI pipeline yet, no dependency lockfile, no request-ID log
  correlation — see `docs/PHASE1_AUDIT_v2.md` for what was still open going
  into Phase 2 and why none of it blocked that decision.
- The Experimentation Engine's four axes (M6) use a documented heuristic
  margin-over-baseline, not a real statistical significance test —
  `ARCHITECTURE.md` §5 itself flags that Phase 2/3 volume likely isn't
  dense enough yet for that to matter in practice.
- Real publishing/analytics providers (TikTok/YouTube/Instagram, M4/M5) are
  structurally complete and unit-tested against mocked HTTP, but have never
  been exercised against a live API in this environment (no provisioned
  app-review credentials) — the same honest posture Phase 1's
  Anthropic/ElevenLabs providers already had.
- Length/posting-time experiment recommendations (M6) have no existing
  downstream column to write to yet (`ARCHITECTURE.md` §5's "feeds Script
  Agent's `target_duration_s` guidance" needs a guidance store this phase
  didn't add) — applying one records human endorsement (`applied_at`/
  `applied_by`) without a further side effect, unlike the hook/niche axes.
