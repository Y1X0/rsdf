# Phase 1 Audit v2 — Post-Patch Re-Audit (v1.1)

**Status:** Re-audit after the v1.1 Stability & Security Patch Release.
**Scope:** All P0/P1 fixes from the v1.1 patch (commit history since
`docs/PHASE1_AUDIT.md`), re-verified against the actual code and test
suite — not against what was merely intended.

---

## 0. Executive Summary

Both critical issues from the v1 audit are fixed, verified, and covered by
regression tests that exercise the real HTTP layer, not just isolated unit
calls — which matters here specifically, because the v1 audit's headline
finding (F1) was originally missed by exactly that gap: a unit test proved
a code path worked while the real request/response cycle silently defeated
it. This re-audit re-derives each claim from the code and test run rather
than accepting the patch's own commit messages as evidence.

All five requested P1 items are also fixed and tested. One of them (F3,
Campaign Intelligence) went slightly beyond the minimum fix: rather than
just adding a write endpoint for niche data, scoring now derives a real
signal from internal data when no manual value has been set, closing the
functional gap the original finding actually cared about, not just the API
surface gap.

Nothing in this patch was scoped to touch the medium/low findings from v1
that weren't on the P0/P1 list (no CI, no pagination, N+1 queries, unpinned
dependencies, etc.) — they remain open exactly as before, and are restated
here for completeness rather than re-analyzed in depth. Two small new
findings surfaced from reviewing the new auth code itself (no rate limiting
on token issuance; JWTs aren't revocable before expiry) — neither blocks
Phase 2.

**Verdict: Go**, with the same operating boundaries as before (see §14) —
this patch changes what those boundaries are, not whether they exist.

---

## 1. Scope & Method

Every "fixed" claim below was checked by (a) reading the actual current
source, not the v1.1 task description, and (b) running the specific
regression test that exercises it, with output captured. The full suite
(115 tests, up from 73) was run clean immediately before writing this
document; `alembic check` was run against a real local Postgres 16
instance for both migrations with zero drift, including a full
downgrade/upgrade round-trip.

---

## 2. Status of Every v1 Finding

| ID | Severity | v1 finding | v1.1 status | Verified by |
|---|---|---|---|---|
| F1 | Critical | Failed request discarded an earlier step's already-completed AgentRun/CostLedger rows | **Fixed** | `test_completed_step_survives_a_later_step_failing_in_the_same_request` (real API, forces TTS-succeeds/render-fails, inspects DB directly afterward) |
| F1-corollary | Critical | Idempotency's FAILED-retry branch was dead code in the real API (only reachable in bare unit tests) | **Fixed** | `test_retrying_after_a_failure_succeeds_through_the_real_api` (real API) + `test_run_idempotent_failed_record_survives_a_rollback_of_the_outer_transaction` (simulates the exact rollback api/deps.get_db performs) |
| (new, found while fixing F1) | — | Research/Script agents' LLM costs were never ledgered into `cost_ledger` at all (only TTS/render were) | **Fixed** | `agent_run()` now derives the CostLedger entry itself for every agent, not just the two callers that remembered to ask |
| F2 | Critical | No authentication or authorization on any endpoint | **Fixed** | `tests/api/test_auth_api.py` (10 tests: missing/malformed/invalid/expired token, wrong role, valid access, `/auth/token` issuance); every route in every router file manually grep-verified to carry `require_auth` or `require_operator` |
| F2 (review endpoint specifically) | High | Any caller could "approve" content as any `reviewer_id` string | **Fixed** | `test_review_endpoint_uses_authenticated_identity_not_request_body` |
| F3 | High | No read/write API for niche saturation/trend/CPM; scoring permanently neutral | **Fixed**, and the underlying functional gap (not just the missing endpoint) is closed | `tests/api/test_niches_api.py` — includes a test proving `competition_level` reflects real internal campaign-count data (0.3, not the coincidental 0.5 default) when no manual value is set |
| F4 | High | `qc_status` hardcoded to `"passed"` unconditionally | **Fixed** | `tests/unit/test_qc_service.py` (7 tests: missing audio, no captions, duration mismatch, missing asset, remote-URL assets skip the disk check, no-target-duration skips that check) |
| F5 | High | Niche/hook check-then-act races, no DB backstop | **Fixed** | `test_get_or_create_record_falls_back_to_winner_on_concurrent_insert_race`, `tests/unit/test_db_safety.py` (3 tests simulating the race directly) |
| F6 | High | Missing indexes on `videos.status`, `campaigns.niche_id` | **Fixed** | `tests/unit/test_schema_indexes.py` (asserts on model metadata directly, so a future accidental removal fails fast) + migration `0002` verified against real Postgres |
| F7 | High | No bounds on cost-sensitive fields, headlined by unbounded `num_variants` | **Fixed** | `tests/api/test_validation.py` (8 tests: `num_variants` 0/11/10 boundary, oversized text, negative rates, out-of-range `completion_rate`, negative cost) |
| F19 | High | No retry/backoff around external provider calls | **Not addressed this round** (wasn't in the P0/P1 list) — F1's fix means a *client* can now safely retry after a transient failure; automatic retry-with-backoff *inside* the provider call itself is still absent |
| F11 | Medium | Two endpoints returned untyped dicts | **Fixed** (bonus, touched while adding auth to the same file) | `CostEntryOut`/`RevenueEntryOut` response models |
| F20 | Medium | `/health` didn't check anything | **Fixed** (bonus) | Checks real DB connectivity via its own short-lived connection, returns 503 on failure |
| F8 | Medium | No CI pipeline | Not addressed — out of scope for this patch | — |
| F9 | Medium | N+1 query patterns on list endpoints | Not addressed; see §9 for a minor related note | — |
| F10 | Medium | No pagination on most list endpoints | Not addressed | — |
| F12 | Medium | No coverage measurement tool | Not addressed | — |
| F13 | Medium | Zero coverage on real Anthropic/ElevenLabs/Pillow provider code | Not addressed (the *new* auth and QC code is fully tested; the three pre-existing real providers remain untested) | — |
| F14 | Medium | No request-ID/correlation-ID in logs | Not addressed | — |
| F15 | Medium | `Numeric` columns typed as `float`, actually return `Decimal` | Not addressed | — |
| F17 | Medium | `learning_patterns.supporting_video_ids` is a JSON array, not a join table | Not addressed | — |
| F16 | Low | Dead columns `rules_json`, `budget_remaining_est` | Not addressed — confirmed still unwritten anywhere | — |
| F21 | Low | Unpinned dependencies, no lockfile | Not addressed (one new dependency, `pyjwt`, added the same unpinned way as everything else) | — |
| F22 | Low | No CORS policy | Not addressed | — |
| F23 | Low | `200` instead of `201` on creation endpoints | Not addressed | — |
| F24 | Low | Forward-looking composite indexes | Not addressed | — |
| F25 | Low | Audit tables mutable at the DB level | Not addressed | — |
| F26 | Low | No API version prefix | Not addressed | — |

---

## 3. New Findings From This Patch

**N1 — [MEDIUM] No rate limiting on `POST /auth/token`.** The shared
`AUTH_CLIENT_SECRET` has no brute-force protection — nothing throttles or
locks out repeated failed attempts against the token endpoint. Low
practical urgency today (Phase 1 is single-operator, not internet-facing),
but this is exactly the kind of gap that matters the moment the API is
reachable from anywhere other than a trusted machine — which is the same
condition the v1 audit's Go/No-Go already made a hard requirement (§14).

**N2 — [LOW] JWTs aren't revocable before natural expiry.** A standard,
accepted tradeoff of stateless tokens, not a bug — mitigated by the default
60-minute expiry. Worth documenting (done, in `docs/PHASE1.md`) rather than
"fixing," since a revocation list would require exactly the persistent
session-state this design intentionally avoids for Phase 1's single-client
reality.

**N3 — [LOW, informational] More frequent mid-request commits mean more
DB round-trips per multi-step request.** `agent_run()`'s per-step commit
(the F1 fix) triggers SQLAlchemy's `expire_on_commit` behavior, so objects
touched again later in the same request (e.g. `script.target_duration_s`
read inside `qc_service` after the TTS and render commits) trigger a fresh
`SELECT` each time. This is the correct trade for durability and is not a
measured problem at Phase 1 volume — noted for completeness, not as an
action item.

**N4 — [LOW, informational] Every action currently attributes to one
shared operator identity.** Phase 1 issues tokens to a single configured
client, so `reviewer_id` and every audit trail will show the same subject
(`test-operator` in tests, whatever `AUTH_CLIENT_ID` is set to in
practice) for every action. This is expected given Phase 1 has exactly one
real operator, not a bug — but it means the audit log's reviewer
attribution isn't yet meaningful multi-user provenance, only proof that
*some* authenticated caller acted. Real per-user attribution needs the
per-user store already flagged as Phase 2+ territory in `docs/PHASE1.md`.

---

## 4. Re-run: Architecture, Security, Production-Readiness (targeted)

Rather than repeat all 12 review dimensions from v1 in full (most of the
codebase didn't change), this section covers only what materially changed.

**Architecture.** The durability-over-atomicity trade (F1) is now the
system's explicit, documented design choice rather than an accidental gap
— `agents/base.py`'s docstring and `docs/PHASE1.md`'s technical-decisions
section both state it plainly, including the tradeoff being made. The auth
layer (`auth/`) is a clean addition: one JWT service module, one dependency
module, applied uniformly, with zero business logic touching a token
directly except through the two dependency functions.

**Security.** F2's fix is complete and verified route-by-route (§2). SQL
injection remains a non-issue (ORM-only, confirmed unchanged). The JWT
implementation restricts to an explicit algorithm allowlist
(`_ALLOWED_ALGORITHMS = ("HS256",)`) rather than trusting the token's own
`alg` header — the standard defense against algorithm-confusion attacks.
Secrets hygiene is unchanged and still clean: `JWT_SECRET_KEY`/
`AUTH_CLIENT_SECRET` follow the same "empty by default, fails closed, never
in git" pattern as every other credential in this codebase.

**Production-readiness.** The new global exception handler
(`api/main.py`) means an unhandled exception now always returns a proper,
non-leaking 500 response with server-side logging, instead of an
unhandled crash — this was also what made F1's regression tests possible
to write against the real HTTP layer in the first place (Starlette's
`ServerErrorMiddleware` always re-raises after invoking a registered
handler; a real ASGI server still delivers the handler's response to the
client regardless, but the test suite needed `TestClient(...,
raise_server_exceptions=False)` to observe it — now configured in
`tests/conftest.py`).

**Scalability.** No material change — F9/F10 (N+1, pagination) remain
exactly as documented in v1, plus the minor N3 note above.

---

## 5. Strengths

- Both critical findings are fixed with regression tests that specifically
  target the *mechanism* of the original bug (real HTTP request/response
  cycle, real rollback simulation), not just the surface symptom — this
  directly addresses the v1 audit's own meta-finding that a unit test had
  previously created false confidence.
- The niche fix (F3) went beyond "add an endpoint" to "make the scoring
  feature actually usable with real data," which is the standard this
  document holds every fix to.
- Every route's auth coverage was verified exhaustively (grep across all
  seven router files), not sampled.
- 42 new tests were added (73 → 115), all exercising the specific fix they
  regress-test, with no reduction in what the original 73 covered — the
  full original suite still passes unchanged.
- The migration is verified with zero drift against real Postgres,
  including a full round-trip, for both `0001` and `0002`.

## 6. Weaknesses

- The medium/low findings from v1 that weren't in this patch's scope are
  unchanged — most notably no CI (F8), no coverage tooling (F12), and zero
  test coverage on the three real external providers (F13).
- N1 (no rate limiting on token issuance) is a real, if low-urgency, gap
  in the new auth surface itself.
- Auth is still single-tier / single-client (N4) — adequate for Phase 1's
  actual usage, but not yet meaningfully multi-user.

---

## 7. Critical Issues

None open. Both critical issues from v1 (F1, F2) are fixed and verified.

## 8. High-Priority Improvements

- F19 (not in this patch's scope): add retry-with-backoff around the
  Anthropic and ElevenLabs calls specifically — F1/F2 make retries *safe*
  now, but a transient provider blip still surfaces as a failed request
  requiring a manual client retry rather than being absorbed automatically.
- N1: add basic rate limiting / lockout on `POST /auth/token` before this
  API is reachable from anywhere beyond a trusted machine.

## 9. Medium-Priority Improvements

Unchanged from v1, restated for completeness: F8 (CI), F9 (N+1 queries),
F10 (pagination), F12 (coverage tooling), F13 (untested real providers),
F14 (request-ID correlation), F15 (Decimal/float type mismatch), F17
(JSON-array pattern-video relationship).

## 10. Low-Priority Improvements

Unchanged from v1: F16 (dead columns), F21 (unpinned dependencies), F22
(no CORS policy), F23 (200 vs 201), F24 (forward-looking composite
indexes), F25 (audit tables not DB-enforced immutable), F26 (no API
version prefix). Plus new: N2 (JWT revocation — documented tradeoff, not
an action item), N3 (informational only).

## 11. Refactoring Recommendations

Carried over from v1, still valid: generalize `services/idempotency.py`'s
single-entity assumption to remove `api/routers/content.py`'s
`_ScriptBatchAnchor` shim; introduce a shared list-endpoint pagination
helper before fixing F10 piecemeal. Nothing new this round — this patch
was scoped to fixes, not restructuring, per the "no new features, don't
introduce unnecessary complexity" instruction it was given.

## 12. Production Risks

- The two risks that made v1's Go conditional — untrustworthy cost/profit
  numbers (F1) and no gate on unauthenticated access (F2) — are both
  closed. The remaining production risk is the same shape as before, one
  tier down: no CI (F8) means a regression can still reach the branch
  without an automated check, and no rate limiting on token issuance (N1)
  is a real gap the moment this leaves a trusted machine.
- No containerization/deployment hardening has been added or reviewed
  (still out of scope, as in v1 — no Dockerfile exists).

## 13. Security Risks

- No confirmed vulnerabilities in the new auth code: algorithm-confusion
  defended against, constant-time credential comparison, fails closed with
  no configured secret, no credential leakage in error messages.
- N1 (no rate limiting on token issuance) is the one real new security
  gap surfaced by this patch, rated Medium rather than High because
  Phase 1's actual deployment boundary (a trusted, non-internet-facing
  machine) already contains the blast radius — but it must be addressed
  before that boundary changes.
- Prompt-injection surface (raw_notes/rules_text into LLM prompts, noted
  in v1) is unchanged — still low severity given trusted operator input,
  still worth tracking before less-trusted input sources exist.

## 14. Go / No-Go Recommendation

**Go**, for the same scope the v1 audit defined: continued Phase 1
validation use, and now with the two conditions that made v1's Go
*conditional* actually satisfied rather than merely promised —

- Cost/profit numbers this system reports **can now be trusted** as
  accurately reflecting what actually happened, including under partial
  pipeline failures — this was the explicit condition v1 attached before
  trusting those numbers for any real decision.
- The API **is no longer open to unauthenticated access** — the other
  explicit condition v1 attached before any deployment beyond a trusted,
  single-operator machine.

Phase 2 is unblocked with respect to the P0/P1 items this release targeted
— all seven are closed and regression-tested. The items still open (§9,
§10) are exactly the ones the v1 audit already rated Medium/Low and this
release was never scoped to touch; none of them block Phase 2 on their
own, and the two new findings from this patch (N1, N2) are the same
severity tier or lower. Recommend picking up F19 (retry/backoff) and N1
(auth rate limiting) early in Phase 2, since Phase 2 explicitly increases
both automation (more unattended provider calls, raising F19's odds of
mattering) and request volume (raising N1's odds of mattering) — but
neither is a gate on starting.
