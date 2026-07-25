# Phase 1 Implementation — Developer Guide

This is the Phase 1 MVP described in `ARCHITECTURE.md` §16/§22-23: manual
campaign input, a working Research Agent and Script Agent, a template-based
production pipeline, a human review workflow, and an analytics/cost/revenue
foundation, exposed over a REST API. It is **not** the full architecture —
publishing to real platforms, account management, the acting Experimentation
Engine, and generative video are all explicitly out of scope until Phase 2/3.

## What's implemented

| Goal | Where |
|---|---|
| 1. Campaign workflow (manual input, storage, scoring) | `db/models/campaign.py`, `services/campaign_scoring.py`, `api/routers/campaigns.py` |
| 2. Research Agent | `agents/research_agent.py` |
| 3. Script Agent | `agents/script_agent.py` |
| 4. Content Intelligence (hook_library, patterns, outcome tracking) | `db/models/hook.py`, `services/content_intelligence.py` |
| 5. Production pipeline (template-based) | `services/production_service.py`, `video_production/` |
| 6. Human Review workflow | `services/review_service.py`, `api/routers/review.py` |
| 7. Analytics foundation (views/retention/engagement/revenue/cost) | `services/analytics_service.py` |
| 8. Dashboard/API | `api/routers/dashboard.py` + FastAPI's built-in `/docs` |

## Setup

Requires Python 3.11+ and a Postgres instance (SQLite also works for local
dev/tests — see "Database portability" below).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# edit .env: set DATABASE_URL, and any real API keys you have. Every key is
# optional — see "Running without any API keys" below.

alembic upgrade head
uvicorn content_factory.api.main:app --reload
```

Then open `http://localhost:8000/docs` for interactive API docs — this
doubles as the Phase 1 "dashboard" per `ARCHITECTURE.md` §22 ("even a
spreadsheet-backed one is fine"); a real Next.js dashboard is Phase 2+.

## Running without any API keys

Every external integration is optional by design (adjustment #6 + the
`resolved_*_provider()` methods in `config.py`):

- No `ANTHROPIC_API_KEY` → the Research/Script Agents use `FakeLLMClient`,
  which returns an empty JSON array/object instead of crashing. You'll see
  `llm_provider_fallback` in the logs. Real generation requires a real key.
- `TTS_PROVIDER=silent` (default) → produces a real (silent) WAV file using
  only the Python standard library — no ElevenLabs key needed.
- `RENDERER_BACKEND=null` (default) → produces a JSON render manifest
  instead of an actual video — no ffmpeg/Pillow needed. This is also what
  the test suite always uses.

This means the entire pipeline — campaign → research → script → render →
review → metrics → profit — is exercisable end to end with zero secrets,
which is also exactly how the test suite works.

To use real providers: set `ANTHROPIC_API_KEY`, and/or
`TTS_PROVIDER=elevenlabs` + `ELEVENLABS_API_KEY` (install with
`pip install '.[elevenlabs]'`), and/or `RENDERER_BACKEND=template_pillow`
(install with `pip install '.[rendering]'`).

## Database portability (Postgres in prod, SQLite in tests)

Every model avoids Postgres-only column features (no native `ENUM` types —
`Enum(..., native_enum=False)` is used everywhere; no `ARRAY` columns — JSON
lists are used instead). This means the exact same `Base.metadata` and the
same Alembic migration produce a working schema on both Postgres
(production/dev — matches `ARCHITECTURE.md` §14) and in-memory SQLite (the
whole test suite, see `tests/conftest.py`). Verified: `alembic check`
reports no drift against a real local Postgres 16 instance.

## Running tests

```bash
pytest
```

73 tests, all against in-memory SQLite with fake/silent/null providers —
no network access, no database server, no API keys required. Structure:

- `tests/unit/` — one module per service/agent, covering the score formulas,
  idempotency edge cases, agent failure handling, and provider behavior in
  isolation.
- `tests/api/` — full HTTP request/response tests through FastAPI's
  `TestClient`, including a complete pipeline walkthrough
  (`test_full_pipeline_idea_to_script_to_render_to_review`).

## A full pipeline walkthrough (curl)

```bash
# 1. Campaign workflow (goal #1)
curl -X POST localhost:8000/campaigns -H 'content-type: application/json' -d '{
  "brand_name": "Acme Corp", "niche_name": "personal_finance",
  "cpm_rate": 4.0, "budget_cap": 5000, "rules_text": "Must disclose sponsorship."
}'
curl -X POST localhost:8000/campaigns/1/score

# 2. Research Agent (goal #2)
curl -X POST localhost:8000/campaigns/1/research -H 'content-type: application/json' \
  -d '{"raw_notes": "competitors are using countdown-style hooks this week"}'

# 3. Script Agent (goal #3) — retrieves hooks from Content Intelligence (goal #4)
curl -X POST localhost:8000/campaigns/1/ideas -H 'content-type: application/json' \
  -d '{"concept_summary": "3 budgeting myths that keep people broke"}'
curl -X POST localhost:8000/ideas/1/scripts -H 'content-type: application/json' -d '{"num_variants": 3}'

# 4. Production pipeline (goal #5) — TTS -> captions -> template renderer
curl -X POST localhost:8000/scripts/1/render -H 'content-type: application/json' -d '{}'

# 5. Human review (goal #6)
curl localhost:8000/videos/pending-review
curl -X POST localhost:8000/videos/1/review -H 'content-type: application/json' \
  -d '{"reviewer_id": "alice", "decision": "approved"}'

# 6. Analytics foundation (goal #7)
curl -X POST localhost:8000/videos/1/metrics -H 'content-type: application/json' -d '{
  "views": 12000, "avg_watch_time_s": 22, "completion_rate": 0.55,
  "shares": 30, "comments": 60, "likes": 800
}'
curl -X POST localhost:8000/videos/1/revenue -H 'content-type: application/json' \
  -d '{"campaign_id": 1, "approved_views": 8000, "payout_realized": 32.0, "status": "paid"}'
curl localhost:8000/videos/1/profit

# 7. Dashboard (goal #8)
curl localhost:8000/dashboard/summary
```

## Major technical decisions

Each of these expands on a line item from the approved implementation plan.

**Modular monolith, not microservices.** `ARCHITECTURE.md` describes an
event-driven, Temporal-orchestrated system for scale. Phase 1's volume
(15-20 videos/month, a human reviewing every one) doesn't need durable
workflow execution or a message queue — it needs correct, testable
synchronous request/response code. The module boundaries (`agents/`,
`services/`, `video_production/`, `api/`) mirror where Phase 2/3 service
boundaries would go, so extraction later is a refactor, not a rewrite.

**Every AI output is versioned through one table, `agent_runs`.** Rather
than bolting versioning fields onto every entity that happens to involve an
LLM/TTS/render call, `agents/base.py`'s `agent_run()` context manager is the
single code path that can write an `AgentRun` row, and it requires
prompt/model/model_version/cost/duration as part of its API — there's no
way to record a partial entry. This is also the audit log
(`ARCHITECTURE.md` §12/§17.4): append-only, and marked `FAILED` with the
error message rather than silently dropped when a provider call raises.

**Idempotency is one reusable mechanism, not three.** `services/idempotency.py`
implements a single `(scope, key) -> fingerprint` pattern used identically
by campaign creation, research triggering, script generation, and
rendering — all four are "the same request should never redo paid work."
When no explicit key is supplied, the request payload's own fingerprint is
used as the key, so accidental duplicate submissions are caught even
without the caller thinking to pass one.

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
"not available yet," not zero.

## Known Phase 1 limitations (intentional, not bugs)

- Whop campaign discovery is fully manual (`POST /campaigns`) — no scraping,
  per `ARCHITECTURE.md` §0's caveat about the lack of a confirmed public API.
- No real platform publishing (TikTok/YouTube/IG) — a video's `status`
  reaches `approved`, and publishing itself is a manual, off-system step
  until Phase 2's API integrations are built.
- Metrics/cost/revenue are entered manually via API — no platform Analytics
  API polling yet.
- Vector/embedding-based hook retrieval is deferred; `content_intelligence.get_top_hooks`
  uses plain SQL filter+sort. Swapping in pgvector similarity search later
  only touches that one function.
- Account health/warmup (`ARCHITECTURE.md` §8) is not built — Phase 1 has no
  concept of "which social account this got posted to" at all yet.
