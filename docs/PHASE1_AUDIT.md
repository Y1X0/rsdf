# Phase 1 Audit — Architecture, Production-Readiness & Security Review

**Status:** Validation phase — audit only, no code changed as part of this document.
**Scope:** Everything committed under Phase 1 (commit `4360a21` on
`claude/ai-automation-architecture-lsddgf`): 8 goals, 8 implementation
adjustments, 73 tests.

---

## 0. Executive Summary

The Phase 1 implementation is honest about its own boundaries — it doesn't
overreach into Phase 2/3 territory, the interfaces it promised (idempotency,
provider abstraction, versioned AI outputs) are real and load-bearing, and
the test suite genuinely exercises the pipeline end to end. That's the good
news, and it's not nothing.

The bad news is concentrated in one place: **the request-scoped,
single-commit database session pattern silently discards successfully
completed, cost-incurring work whenever a *later* step in the same request
fails.** For a system whose entire value proposition is "every AI output is
versioned and every dollar is tracked," that's a direct hit on the two
requirements the user cared about most when approving this phase. It's
fixable without a rewrite, but it needs to be fixed before the cost/revenue
numbers this system produces are trusted for anything real.

The second concentrated problem is that **the API has no authentication at
all**, which is normal for a Phase 1 prototype running on localhost, but
must not be forgotten before this touches a shared environment.

Neither of these invalidates the phase. Both are addressed with scoped,
well-understood fixes — see §16 (Critical Issues) and the Go/No-Go
recommendation in §23.

---

## 1. Scope & Method

This audit is based on re-reading the actual committed source (models,
services, agents, routers, tests, migration) — not on recalling intent from
the design conversation — and on running `grep`/manual trace-throughs to
confirm specific claims (e.g., "is there an index here," "is this field
ever written") before listing them as findings. Every finding below cites
the concrete file/behavior it's based on. No coverage-measurement tool
(`pytest-cov`) is configured, so test-coverage claims are qualitative
(which code paths have an exercising test), not a percentage — that gap is
itself flagged in §12.

---

## 2. Architecture Review

The modular-monolith shape (`agents/`, `services/`, `video_production/`,
`api/`) cleanly mirrors the service boundaries ARCHITECTURE.md describes for
later phases, and the dependency direction is correct throughout: routers
depend on services, services depend on interfaces (`LLMClient`,
`TTSProvider`, `VideoRenderer`), and only three factory modules ever import
a concrete provider. This is genuinely well executed — a grep for
`import anthropic`, `import elevenlabs`, `import httpx` (in the TTS
context), and `from PIL` outside their respective provider files returns
nothing.

The one architectural tension the implementation surfaces (and doesn't
resolve) is **atomicity vs. durability at the request boundary** — see
finding **F1** in §3. A single request-scoped SQLAlchemy session with one
commit at the end gives clean atomicity (a half-failed request leaves no
partial campaign/video/script behind) but the wrong failure mode for a
system where some of the steps inside that transaction correspond to
*real-world, non-refundable side effects* (an LLM call that was actually
billed, a TTS call that was actually made). Atomicity is the right default
for pure-data operations; it's the wrong default when a step has already
spent money by the time a later step fails.

**Findings:** F1 (Critical), F19 (High) — see §16/§17.

---

## 3. Production-Readiness Review

**F1 — [CRITICAL] Failed requests silently discard already-completed,
cost-incurring work.**
`api/deps.py`'s `get_db()` commits once, after the whole request completes,
and rolls back everything on any exception. `services/production_service.py`
renders a video in two steps inside one request: TTS first, then the
renderer. If TTS succeeds (a real ElevenLabs call, in a Phase 2+ deployment,
already billed) and the renderer then raises, the *entire session* rolls
back — including the `AgentRun` row that recorded the TTS call's cost, and
the `CostLedger` entry derived from it. The money was spent; the record of
it having been spent disappears. The same pattern applies to any
multi-agent-run endpoint as more are added.

A second consequence, confirmed by re-reading `services/idempotency.py`
against `api/deps.get_db`: the "mark FAILED, allow retry" branch of
`run_idempotent` is **exercised only by the unit tests that call it
directly against a bare `db_session` fixture** (`tests/unit/test_idempotency.py::test_run_idempotent_allows_retry_after_failure`).
In the real API path, a failure rolls back the `IdempotencyRecord` insert
too, so there is no FAILED record left to retry — the retry path is
correct in isolation but **effectively dead code in production**, and
nothing in the API test suite catches this because API tests only exercise
success paths for these endpoints. This is exactly the kind of gap "review
test coverage and identify missing edge cases" (§12) is supposed to catch,
and it did.

**F2 — [CRITICAL] No authentication or authorization on any endpoint.**
Every route in `api/routers/*.py` is unauthenticated. `reviewer_id` on
`POST /videos/{id}/review` is a free-text string the caller supplies —
anyone who can reach the API can "approve" content as anyone. This is a
reasonable, deliberate simplification for a single-operator local Phase 1
tool, but it is not scoped to be safe on a shared network today.

**F7 — [HIGH] No bounds on cost-sensitive request fields.**
`ScriptGenerateRequest.num_variants` (`schemas/content.py`) has no upper
bound — a client can request an arbitrarily large number of script variants
in one call, multiplying LLM cost with no server-side ceiling. `raw_notes`,
`rules_text`, and `concept_summary` are unbounded `str`/`Text` fields with
no `max_length`, so a large payload directly inflates the prompt sent to
the LLM (and, with a real key configured, the bill). The Cost Control
Layer described in ARCHITECTURE.md §10 is explicitly a Phase 2+ item, but
*some* server-side sanity ceiling belongs in Phase 1 regardless, since it's
a one-line `Field(le=..., max_length=...)` change, not a new system.

**F19 — [HIGH] No retry/backoff around external provider calls.**
`llm/providers/anthropic_provider.py` and
`video_production/tts/providers/elevenlabs_provider.py` make a single
attempt with no retry, no backoff, and no explicit timeout override (relies
on library defaults). Combined with F1, a transient provider hiccup mid-
pipeline is both unretried automatically *and* erases the record of
whatever already succeeded in that request.

**F20 — [MEDIUM] `/health` doesn't check anything.**
`api/main.py`'s health endpoint returns `{"status": "ok"}` unconditionally
— it doesn't verify DB connectivity. A load balancer or orchestrator using
this for liveness/readiness would report healthy even with a dead database
connection pool.

**F8 — [MEDIUM] No CI pipeline.** ARCHITECTURE.md §14 names GitHub Actions
as the CI/CD choice; none exists in this repo yet, so the 73 tests only run
when a human remembers to run them locally before pushing.

---

## 4. Security Review

**Strength:** no hardcoded secrets anywhere (`grep -rn "sk-\|api_key.*="` on
`src/` turns up only the `Settings` field definitions, all defaulting to
empty string), `.env` is gitignored, `.env.example` ships no real values,
and every provider factory treats "no key configured" as a first-class,
tested fallback path rather than crashing. SQL injection is a non-issue —
every query goes through the SQLAlchemy ORM/Core with bound parameters; no
raw string-interpolated SQL exists in the codebase.

**F2 — [CRITICAL] No auth** — see §3. Repeated here because it's the
single biggest security gap: it's not "missing hardening," it's "missing
the concept entirely."

**F7 — [HIGH] Unbounded inputs are also a resource-exhaustion vector**, not
just a cost problem — see §3.

**Prompt injection surface (new finding, not previously flagged).**
`raw_notes` (Research Agent input) and `campaign.rules_text` are
interpolated directly into LLM prompts in `agents/research_agent.py`'s
`_build_prompt` with no sanitization or delimiter escaping. Since these
fields are operator-supplied in Phase 1 (not third-party input), the
practical risk today is low — but the pattern doesn't defend against a
compromised or careless input source, and the Quality Scoring System's
policy-risk check (`services/quality_scoring.py`) is a plain keyword scan
that inherits the same weakness: adversarial phrasing can trivially evade
it. Worth a note now so it isn't forgotten once campaign data starts coming
from less-trusted sources (e.g., a scraped Whop feed in Phase 2).

**F25 — [LOW] Audit tables are append-only by convention, not by
enforcement.** Nothing at the database level prevents `UPDATE`/`DELETE` on
`agent_runs` or `review_decisions` — the immutability ARCHITECTURE.md §17.4
asks for is only as strong as the service-layer code's discipline today.

**F21 — [LOW] Dependencies are unpinned.** Every entry in `pyproject.toml`
uses `>=` with no lockfile (no `uv.lock`/`poetry.lock`/hash-pinned
requirements). A future `pip install` can silently pull a newer,
potentially breaking or compromised transitive dependency.

**F22 — [LOW] No CORS policy configured** — irrelevant until a browser
client exists, but worth remembering before one is added.

---

## 5. Scalability Review

Phase 1's target volume (15–20 videos/month, human-gated) makes essentially
none of this urgent today, but the audit is asked to look ahead:

**F9 — [MEDIUM] N+1 query patterns.** `api/routers/campaigns.py`'s
`_to_campaign_out` accesses `campaign.scores[0]` per campaign in
`list_campaigns`, and `api/serializers.py`'s `to_video_out` issues one
extra `QualityScore` query per video in `list_videos`/`list_pending_review`.
Neither uses `joinedload`/`selectinload` — confirmed via `grep -rn
"joinedload\|selectinload" src/`, which returns nothing anywhere in the
codebase. Fine at today's row counts; will show up in profiling once a
campaign has dozens of videos.

**F10 — [MEDIUM] No pagination anywhere except a bare `limit` on
`GET /hooks`.** `list_campaigns`, `list_ideas`, `list_scripts`, and
`list_videos` all return every matching row, unbounded. This is the first
thing that will need to change once any of these tables grows past a few
hundred rows.

**Async/threadpool ceiling (contextual note, not yet a bottleneck).** All
routes are synchronous `def` functions; FastAPI runs them in Starlette's
default threadpool. Combined with blocking HTTP calls to Anthropic/
ElevenLabs (once real keys are configured), concurrent request volume will
eventually be bounded by threadpool size rather than CPU. Not a Phase 1
problem at current volume; worth remembering before Phase 2 raises
concurrency.

**Provider singletons are process-local (`api/deps.py`'s `@lru_cache`
factories)** — correct and intentional for a single-process deployment; no
finding here, just confirming it was checked and is fine as designed.

---

## 6. Maintainability Review

The codebase is consistent in style and every non-obvious decision has an
inline comment pointing back to the relevant ARCHITECTURE.md section — that
traceability is a real asset for anyone picking this up cold.

**F15 — [MEDIUM] `Numeric` columns are type-hinted as `float` but return
`Decimal` at runtime.** `AgentRun.cost_usd`, `ViralScoreRecord.score`,
`CostLedger.cost_usd`, `RevenueSnapshot.payout_realized`, and
`MetricsSnapshot`'s numeric fields are all declared
`Mapped[float] = mapped_column(Numeric(...))`. SQLAlchemy's `Numeric` type
returns `decimal.Decimal` by default (not `float`), so the Python-level type
hints don't match what the ORM actually hands back. The code mostly
compensates with explicit `float(...)` casts at read sites (and the test
suite passes, since Python's numeric tower tolerates the mixing), but it's
a latent type-confusion source for anyone who trusts the annotation over
the runtime behavior. Fix is mechanical: either add `asdecimal=False` to
each `Numeric(...)` or change the annotations to `Decimal` consistently.

**F16 — [LOW] Two schema columns are defined but never written anywhere.**
`Campaign.rules_json` and `Campaign.budget_remaining_est` exist on the
model (confirmed via `grep -rn "rules_json\|budget_remaining_est" src/` —
only the model definitions themselves match) but no create/update path
ever populates them. Either wire them up or drop them; a column nobody
writes to is a small but real maintenance trap for the next person who
assumes it's populated.

**Complexity wart:** `api/routers/content.py`'s `_ScriptBatchAnchor`
dataclass exists purely to make a multi-row result (N generated scripts)
fit `services/idempotency.py`'s single-entity-with-an-`.id` interface. It
works and is tested, but it's a visible seam — see §10 (Refactoring
Recommendations) for the suggested generalization.

---

## 7. Unnecessary Complexity

There isn't much to report here — this is one of the stronger areas.
Nothing in the codebase is over-abstracted relative to what it does. The
one artifact worth naming is the `_ScriptBatchAnchor` shim above; everything
else (the provider interfaces, the idempotency service, the agent-run
recorder) earns its abstraction by actually being used more than once and
by directly implementing a named requirement from the approved plan.

---

## 8. Technical Debt

Consolidated from findings elsewhere in this document, for a single
"debt ledger" view: F1 (transaction/durability mismatch), F3 (niche fields
write-only-in-theory — see §10), F15 (Decimal/float mismatch), F16 (dead
columns), F17 (JSON-array relationship instead of a join table — see §11),
the missing `_ScriptBatchAnchor` generalization (§6), and the absence of any
lockfile (F21). None of these require a rewrite; all are scoped,
independent fixes.

---

## 9. Performance Bottlenecks

At Phase 1's actual expected volume, there are no real bottlenecks — this
section is forward-looking, not an active-fire report. In order of when
they'd first bite: N+1 queries on list endpoints (F9) once a campaign
accumulates dozens of videos; unpaginated list endpoints (F10) at the same
point; the synchronous threadpool ceiling under concurrent real-provider
calls (§5); and `services/quality_scoring.py`'s originality check, which
does an *unindexed* full scan of `Campaign` filtered by `niche_id` (see
F6) on every single render — cheap today, a measurable cost once campaign
counts grow, because it currently forces a table scan rather than an index
lookup.

---

## 10. API Design Consistency

**F11 — [MEDIUM] Inconsistent response typing.** Every endpoint in
`api/routers/*.py` declares a Pydantic `response_model` *except*
`POST /videos/{id}/cost` and `POST /videos/{id}/revenue` in
`api/routers/analytics.py`, which return bare `dict`s. No OpenAPI schema is
generated for these two responses, and there's no validation that the
returned shape stays correct as the code evolves. Trivial fix: give them
proper `CostEntryOut`/`RevenueEntryOut` schemas like every other endpoint.

**F3 — [HIGH] Niche management is a dead end.** There is no
`GET /niches`, `GET /niches/{id}`, or any update endpoint for a niche at
all (confirmed: `grep -rn "niches" src/content_factory/api/routers/`
matches nothing). Niches are only ever created implicitly, bare (`name`
only), via `POST /campaigns`'s `niche_name` field. Since
`services/campaign_scoring.py`'s `compute_competition_level` and
`compute_niche_fit_score` read `Niche.saturation_score`/`trend_score`, and
there is no way to ever set those fields through the API, **Campaign
Intelligence's composite score is permanently running on the neutral 0.5
default for both inputs in any real usage of this system today.** This
isn't a documentation gap — it's a functional gap in the one feature (goal
#1's scoring) the Validation phase should care most about, because it means
the scoring system, as shippable today, cannot actually differentiate
saturated from uncontested niches no matter how much real-world data an
operator has.

**Minor conventions worth normalizing, not urgent:**
- Creation endpoints (`POST /campaigns`, `POST /campaigns/{id}/ideas`, etc.)
  all return `200` rather than the conventional `201 Created` (F23, Low) —
  a deliberate simplification for the idempotency-replay endpoints, but
  applied uniformly even to non-idempotent creates (`create_idea`) where
  there was no reason not to use `201`.
- No API version prefix (`/v1/...`) anywhere (F26, Low) — fine for an
  internal Phase 1 tool, worth adding before any external consumer depends
  on this API's shape.

---

## 11. Database Normalization & Indexing

The schema (`alembic/versions/0001_initial_schema.py`, generated from and
verified against the models with zero drift via `alembic check`) is in good
3NF shape overall: each table represents one entity or one event, and the
append-only event tables (`metrics_snapshots`, `cost_ledger`,
`revenue_snapshots`, `agent_runs`, `review_decisions`) are correctly
separated from the mutable entity tables rather than overwriting history.

**F6 — [HIGH] Two missing indexes on columns that are actually queried.**
- `videos.status` has no index, but it's filtered directly in
  `list_pending_review` (`WHERE status = 'pending_review'`) and grouped by
  in `analytics_service.get_dashboard_summary` (`GROUP BY status`).
- `campaigns.niche_id` has no index, but `quality_scoring.compute_originality_score`
  filters `Campaign.niche_id == niche_id` directly (joining
  `Script → ContentIdea → Campaign`) on **every single render request**.
  This is not a hypothetical future query — it's live, hot-path code today.

**F5 — [HIGH] Two check-then-act races with no database-level backstop.**
- `api/routers/campaigns.py`'s `_get_or_create_niche` does a `SELECT` then
  an `INSERT` with no transaction-level guard; under concurrent requests
  creating the same new `niche_name`, one request hits the `niches.name`
  unique constraint as an unhandled `IntegrityError` (surfaces as a
  500, and — per F1 — rolls back the whole request).
- `services/content_intelligence.py`'s `find_or_create_hook` has the same
  check-then-act shape, but **there is no unique constraint on
  `(niche_id, hook_text)` at all** — under concurrency this can silently
  create duplicate `hook_library` rows for identical text rather than
  erroring, which is arguably worse (a silent data-quality problem instead
  of a loud one).

**F17 — [MEDIUM] `learning_patterns.supporting_video_ids` is a JSON array,
not a join table.** A reasonable simplification at Phase 1 volume (avoids
a fourth table for a rarely-queried relationship), but it means "which
patterns does video X support" requires either an application-side scan or
an unindexed JSON-containment query — it won't survive real Phase 2/3
volume without becoming a proper `learning_pattern_videos` join table.

**F24 — [LOW] Forward-looking composite indexes.** `hook_library`'s
retrieval query (`content_intelligence.get_top_hooks`) orders by
`best_viral_score DESC NULLS LAST, created_at DESC` with only a single-
column index on `niche_id` — a composite `(niche_id, best_viral_score)`
index would matter once the table has real volume, not today.
`cost_ledger` similarly has no composite index for "costs by category over
a time window," a query shape the Cost Control Layer will want in Phase 2.

---

## 12. Test Coverage & Missing Edge Cases

73 tests currently pass, covering the score formulas, idempotency's
sequential-retry behavior, agent failure handling (mocked failures), and a
full HTTP pipeline walkthrough. That's real, meaningful coverage of the
happy paths and of several designed-in failure paths. The gaps:

**F13 — [MEDIUM] Zero coverage on the three real provider implementations.**
`grep -rln "anthropic_provider\|elevenlabs_provider\|template_pillow"
tests/` returns nothing. `AnthropicLLMClient`, `ElevenLabsTTSProvider`, and
`TemplatePillowRenderer` are entirely untested — every test exercises the
fake/silent/null path instead. This is defensible (no real credentials
belong in a test suite), but it means a bug in the *real* integration code
— the code path that only runs once a real key is configured — wouldn't be
caught until someone manually exercises it in a live environment. At
minimum, these should get tests that mock the underlying HTTP/SDK call
(e.g., `unittest.mock` on `anthropic.Anthropic.messages.create`) rather
than mocking the whole provider away.

**F12 — [MEDIUM] No coverage measurement tool.** `pytest-cov` isn't in
`pyproject.toml`'s dev dependencies and no coverage threshold is enforced
anywhere. "73 tests pass" is not the same claim as "X% of branches are
exercised," and right now there's no way to answer the second question
except by manual inspection (which is how this audit found F1's dead-code
retry path).

**Missing edge-case tests, concretely:**
- No test for `num_variants <= 0` or an implausibly large value (ties to
  F7 — there's also no *behavior* to test yet, since no validation exists).
- No test for concurrent/duplicate `niche_name` submission (ties to F5) —
  hard to simulate in a synchronous test client, but even a direct
  service-level test hitting the same race would document the current
  broken behavior.
- No test exercising `api/main.py`'s exception handlers in isolation
  (only indirectly, via one campaign-idempotency-conflict test).
- No test for malformed JSON / Pydantic validation failures (422 responses)
  on any endpoint.
- No test for `get_dashboard_summary` with more than one video or with a
  mix of video statuses — the current test only checks a single-video case.
- No test proving the *actual* rollback behavior described in F1 through
  the real `client` fixture (e.g., forcing the renderer to fail mid-render
  via the API and asserting what remains in the database afterward) — this
  is the single most valuable test to add next, because it would have
  caught F1 directly.

---

## 13. Logging, Observability & Failure Recovery

**Strength:** structured JSON logging via `structlog` is used consistently
— every agent run, every review decision, every score computation logs a
structured event with the relevant IDs, and `agents/base.py`'s
`agent_run()` context manager guarantees a start/complete-or-fail log
triple around every external call. "No silent failures" (adjustment #4) is
genuinely upheld at the logging layer: a grep for bare `except Exception:
pass` or similar swallowed exceptions across `src/` finds none.

**F14 — [MEDIUM] No request-ID/correlation-ID.** Nothing injects a
per-request identifier into `structlog`'s context, so under concurrent load
there's no cheap way to filter a log stream down to "everything that
happened for this one HTTP request." Fine reading logs from a single
sequential test run; will matter the moment two requests are in flight at
once.

**F20 — [MEDIUM] Shallow health check** — see §3.

**Failure recovery, the good part:** the atomic-per-request commit pattern
means a failed request never leaves a *half-created* campaign, idea, or
script behind — the design correctly prioritizes "no partial garbage data"
over "preserve everything that happened." The bad part is F1: that same
correct-for-data behavior is wrong for cost/audit records that represent
already-incurred real-world side effects. This is the same underlying
mechanism showing up as a strength and a critical weakness depending on
which requirement you're checking it against — worth stating explicitly
since it's not a contradiction, it's a single design choice with two
different consequences.

---

## 14. Strengths

- Provider abstraction is real, not decorative: three factories are the
  *only* places a concrete provider is imported; every test proves this by
  running the entire pipeline on fakes with zero network access.
- Every AI-generated output is versioned through one consistent mechanism
  (`AgentRun`), not bolted onto each entity ad hoc — this is architecturally
  the right call even though its interaction with the commit boundary (F1)
  needs a fix.
- No silent failures at the logging/status layer: every agent and service
  either succeeds visibly or fails visibly, with a structured log line and
  a `FAILED` status persisted (modulo F1's rollback caveat).
- Idempotency is one reusable mechanism applied consistently to every
  workflow-triggering action, not three bespoke unique-constraint hacks.
- Secrets hygiene is clean: nothing hardcoded, `.env` ignored, every
  provider degrades safely with no key configured.
- Database schema is portable by design (no Postgres-only types) and
  verified with zero drift against a real Postgres instance — this is a
  real, checked claim, not an assumption.
- SQL-injection-proof by construction (ORM/Core parameter binding
  throughout, no raw interpolated SQL anywhere).
- 73 tests genuinely exercise services, agents, providers, and a full
  cross-router HTTP pipeline — not just import-and-assert-true smoke tests.
- Every non-obvious decision in the code has an inline comment tracing back
  to the specific ARCHITECTURE.md section it implements — a real
  maintainability asset.

## 15. Weaknesses

- The commit/rollback boundary loses already-incurred cost and audit data
  on partial multi-step failure (F1) — the single most important weakness
  in this audit.
- No authentication anywhere (F2).
- Campaign Intelligence's scoring inputs (niche saturation/trend) have no
  write path, so the scoring feature can't use real data today (F3).
- The "automated QC" field (`qc_status`) doesn't actually check anything —
  it's hardcoded to `"passed"` (F4).
- Two check-then-act races with no database-level backstop (F5).
- Two indexes missing on columns in live query paths, not hypothetical ones
  (F6).
- No bounds on cost-sensitive request fields (F7).
- No CI, no coverage measurement, and zero tests on the real (non-fake)
  provider implementations (F8, F12, F13).
- No pagination on most list endpoints, and N+1 query patterns on the ones
  that assemble nested data (F9, F10).

---

## 16. Critical Issues

1. **F1 — Cost/audit data loss on partial request failure.** Fix direction:
   commit (or use an independent short-lived connection/session) immediately
   after each externally-effectful step — most naturally, inside
   `agents/base.py`'s `agent_run()` context manager itself, so an
   `AgentRun`'s completion (and its derived `CostLedger` entry) is durable
   the moment the external call actually succeeds, independent of whatever
   happens later in the same HTTP request. A `SAVEPOINT`-based nested
   transaction per step is the alternative if a single shared session must
   be kept.
2. **F2 — No authentication.** Fix direction: add a minimal auth layer
   (API key header check is sufficient for an internal tool) before this
   API is reachable from anywhere other than the operator's own machine.
   Not urgent for continued local Phase 1 use; blocking for any shared
   deployment.

## 17. High-Priority Improvements

- **F3** — Add `GET/PATCH /niches` (at minimum a way to set
  `saturation_score`/`trend_score`/`avg_cpm_est`) so Campaign Intelligence's
  scoring can ever reflect real data.
- **F4** — Either implement a real automated QC check (duration match,
  audio/caption sync) or rename/remove `qc_status` so it stops implying a
  verification that isn't happening.
- **F5** — Add a unique constraint on `hook_library(niche_id, hook_text)`
  and catch/handle the `IntegrityError` race in `_get_or_create_niche` (and
  the new hook constraint) with a retry-on-conflict pattern.
- **F6** — Add indexes on `videos.status` and `campaigns.niche_id`.
- **F7** — Add `Field` bounds (`max_length`, `le`/`ge`) to
  `ScriptGenerateRequest.num_variants` and the free-text campaign/research
  fields.
- **F19** — Add retry-with-backoff around the Anthropic and ElevenLabs
  calls (even a simple fixed-attempt retry with jitter is a large
  improvement over zero retries).

## 18. Medium-Priority Improvements

- **F8** — Add a GitHub Actions workflow running `pytest` (and
  `alembic check`) on every push/PR.
- **F9 / F10** — Add `selectinload` for the two known N+1 spots and add
  `limit`/`offset` query parameters to every list endpoint.
- **F11** — Give `submit_cost`/`submit_revenue` proper `response_model`
  schemas instead of bare dicts.
- **F12 / F13** — Add `pytest-cov` with a tracked (not necessarily gated)
  threshold, and add mocked-HTTP tests for the three real provider classes.
- **F14** — Add a request-ID middleware that binds a UUID into
  `structlog`'s contextvars for the duration of each request.
- **F15** — Resolve the `Numeric`/`float` type mismatch (either
  `asdecimal=False` or consistent `Decimal` typing) across all monetary/
  score columns.
- **F17** — Plan the `learning_patterns.supporting_video_ids` → join-table
  migration before Phase 2 volume makes the JSON-array approach a query
  bottleneck.
- **F20** — Make `/health` actually check DB connectivity (a cheap
  `SELECT 1`).

## 19. Low-Priority Improvements

- **F16** — Either wire up `Campaign.rules_json`/`budget_remaining_est` or
  remove them.
- **F21** — Pin dependencies / add a lockfile.
- **F22** — Add an explicit (even if permissive) CORS policy before any
  browser client is built against this API.
- **F23** — Use `201 Created` consistently for non-idempotent creation
  endpoints (e.g., `create_idea`).
- **F24** — Add the forward-looking composite indexes noted in §11 once
  real data volume makes them measurable.
- **F25** — Consider DB-level grant restrictions (`REVOKE UPDATE, DELETE`)
  on the audit-log tables so "append-only" is enforced, not just conventional.
- **F26** — Add an `/v1` prefix before any external consumer depends on
  this API's current shape.

## 20. Refactoring Recommendations

- **Generalize `services/idempotency.py` to support multi-entity results
  directly** (e.g., accept `entity_ids: list[int]` instead of assuming a
  single `.id`), removing the need for `api/routers/content.py`'s
  `_ScriptBatchAnchor` shim.
- **Extract cost/audit persistence out of the request-scoped session**
  (directly addresses F1) — likely as a small "durable event recorder"
  that commits independently, called from `agents/base.py`'s `agent_run()`.
- **Introduce a thin `niches` service + router** (addresses F3) rather than
  letting niche creation remain an implicit side effect of campaign
  creation with no corresponding read/update surface.
- **Add a shared "list endpoint" helper** (pagination params + response
  envelope) so F10's fix is applied uniformly rather than endpoint-by-
  endpoint, avoiding a new inconsistency while fixing an old one.

## 21. Production Risks

- Deploying this today outside a trusted, single-operator environment
  risks: unauthenticated access to approve/reject content and submit
  arbitrary financial figures (F2); silently incomplete cost/audit records
  after any transient provider failure (F1); and uncontrolled LLM spend
  from an unbounded `num_variants` or oversized text field (F7).
- No CI (F8) means a regression can reach the deployed branch without any
  automated check having run.
- No containerization/deployment hardening has been reviewed (out of scope
  for this Phase 1 code audit, since no Dockerfile exists yet) — flagged
  here only so it isn't forgotten before an actual deployment target is
  chosen.

## 22. Security Risks

- **F2** (no auth) is the headline risk — everything else is secondary
  until this is addressed for any non-local deployment.
- **F7**'s unbounded inputs double as a resource-exhaustion/cost-DoS vector,
  not just a data-quality issue.
- **Prompt injection surface** via `raw_notes`/`rules_text` (§4) — low
  severity today given trusted operator input, but should be tracked
  before campaign data comes from a less-trusted source.
- **F25** (audit tables mutable at the DB level) weakens the "defend
  against platform policy/appeals disputes" guarantee ARCHITECTURE.md §17.4
  asks for — the log is currently trustworthy only because the application
  code is well-behaved, not because the database enforces it.
- **F21** (unpinned dependencies) is a supply-chain hygiene gap, not an
  active vulnerability today.
- No confirmed vulnerabilities in the SQL layer, secrets handling, or
  provider-selection logic — these were specifically checked, not just
  assumed clean.

## 23. Go / No-Go Recommendation

**Conditional Go** for continued Phase 1 validation use, **No-Go** for any
deployment beyond a trusted, single-operator, local/internal environment
until F1 and F2 are addressed.

Concretely:
- **Safe to continue using today** for what Phase 1 was scoped for: a
  single operator, on a trusted machine, validating the campaign → research
  → script → render → review → analytics loop with real or fake providers,
  and treating any cost/profit numbers produced *before F1 is fixed* as
  provisional rather than authoritative.
- **Do not** expose this API beyond that trusted context until F2 (auth) is
  addressed — there is no gate stopping an arbitrary caller from approving
  content or fabricating revenue figures.
- **Do not** rely on the cost/profit numbers this system reports for a real
  financial or go/no-go business decision until F1 is fixed — right now, a
  partial pipeline failure can make the system under-report real spend
  without any error being visible in the data itself (the request fails
  loudly; the *cost record of the successful step before it* disappears
  quietly).
- **Before Phase 2 automation begins**, F3 (niche scoring dead end), F5
  (races), and F6 (missing indexes) should be resolved — Phase 2 explicitly
  builds on Campaign Intelligence's scoring being real (ARCHITECTURE.md
  §16's Phase 2 column assumes automated scoring is trustworthy) and on
  higher request volume than Phase 1's human-gated pace, which is exactly
  where the races and missing indexes stop being theoretical.

No architectural rework is required — every finding in this document has a
scoped, independent fix. This is a solid Phase 1 with a short, well-defined
punch list standing between it and being trustworthy at the next phase's
scale.
