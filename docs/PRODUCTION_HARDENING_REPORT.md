# Production Hardening Sprint — Final Report

**Scope:** Response to `docs/PRODUCTION_READINESS_REVIEW.md`'s **NOT READY FOR PRODUCTION** verdict. Eight milestones (H1-H8), P0-then-P1 ordered, each with regression tests and full-suite verification before moving to the next. This report is the gate the user asked for: Phase 3 does not start until this document recommends READY.

**Bottom line: READY**, for the deployment shape this system is actually built for — a single-operator, 3-5-account, ~15-20-videos/month internal tool (ARCHITECTURE.md's stated scope) — with the residual risks in §3 explicitly accepted or scheduled, not hidden.

---

## 1. What changed, milestone by milestone

Each item below names the production readiness review finding(s) it closes, using the review's own codes.

### H1 — Containerization (P0)
- Multi-stage `Dockerfile` (builder installs from a pinned lockfile into a venv; runtime copies it, runs as non-root, `HEALTHCHECK` against `/health`). Closes **I1**.
- `requirements-lock.txt` — 80 pinned packages, generated from `pip install -e ".[dev,production]"`. Closes **DEP1**.
- `docker-compose.yml` — postgres + redis + a one-off `migrate` profile + `app`, wired together for a local production-like topology.
- `Settings.validate_production_safety()` — fail-closed at boot if `ENVIRONMENT=production` and `DATABASE_URL` is SQLite or `JWT_SECRET_KEY` is missing/short. Closes **I6** (the SQLite half) and **S4** (JWT secret length).
- `pool_pre_ping=True` on the engine. Closes **SC2**'s cheap half.
- `docs/DEPLOYMENT.md`.

### H2 — CI/CD pipeline (P0)
- `.github/workflows/ci.yml`: install from lockfile → migrate → migration-drift check → full round-trip → `pytest` → `pip-audit` → `bandit` → container build. Closes **CQ4**, **I2**, and (via `pip-audit`) **S8**.

### H3 — Database production safety (P0)
- `docs/DATABASE_OPERATIONS.md`: managed-Postgres recommendation, backup retention tiers, restore runbook. Closes **DR1**, **DR3** (via the managed-service recommendation).
- `scripts/backup_postgres.sh` / `restore_postgres.sh` — **actually run** against real local Postgres: a dump produced, restored into a fresh database, row counts verified, and the safety guard (refuses a second restore without `FORCE=1`) confirmed. Closes **DR2**.
- `services/media_backup.py` — best-effort S3 backup of rendered assets, off by default. Closes **DR4**.

### H4 — Distributed safety (P1)
- `auth/redis_rate_limiter.py` + `rate_limiter_factory.py` — Redis-backed shared rate limiting, falling back to the existing in-process limiter. **Verified against a real local Redis instance** (two separate limiter objects sharing one counter). Closes **S2**.
- `budget_governor.py::enforce_budget` now takes a `pg_advisory_xact_lock` before checking a ceiling. **Verified with a real-Postgres, real-threads concurrency test** that fails without the lock and passes with it. Closes **D2**/**C1**.
- Documented (not code-changed) why the LLM/TTS/renderer/notification/media-backup singletons need no equivalent fix — they're stateless factories, `@lru_cache` already scopes them per-process. Closes **SC1**.

### H5 — Database optimization (P1)
- Migration `0008`: indexes on `cost_ledger.recorded_at` and `experiment_results.is_winner`; a composite `(account_id, status, published_at)` index on `publications` replacing three narrower ones; drops `idempotency_records`' redundant standalone `scope`/`key` indexes. Closes **D1**, **D4**.
- `limit`/`offset` pagination (default 50, max 200) added to all 14 list endpoints via one shared `api/pagination.py` dependency. Closes **P3**.
- Regression tests: `test_schema_indexes.py` asserts the index/constraint changes against model metadata directly; one pagination test per touched endpoint.

### H6 — Observability (P1)
- `observability.py`: Prometheus `/metrics` (on by default, no-op without the extra installed); Sentry error tracking (inactive until `SENTRY_DSN` is set). Closes **M1**, **M3**.
- Request-ID correlation middleware — binds `X-Request-ID` to `structlog`'s contextvars for the request's duration, echoed in the response header. Closes **M2**.
- `/health` now reports a per-dependency `checks` object (database always; Redis when configured) instead of a bare `{"status": "ok"}`. Partially addresses **M5** (adds the Redis check M5 asked for; does not add M5's disk-space check — see §3).
- `docs/OBSERVABILITY.md`: metrics/error-tracking/health-check/request-correlation reference, deployment-log guidance, and a concrete alerting-strategy recommendation. Closes **M6**; gives **M4** a documented strategy without wiring actual paging infrastructure (inherently deployment-specific — see §3).

### H7 — External provider verification plan (P1)
- `docs/PROVIDER_VERIFICATION_PLAN.md` — per-platform (TikTok/YouTube/Instagram) sandbox and live verification checklists, naming the specific simplification each provider carries (TikTok's and Instagram's skipped polling steps, YouTube's not-yet-real upload mechanism) as the first thing to resolve before live use. Documentation only, per the user's explicit instruction — no provider interface changed. Addresses **E3** with a plan; does not close it, since live credentials don't exist in this environment (see §3).

### H8 — Final verification (this milestone)
Running the full verification surfaced two **real, previously-undiscovered bugs in the H2 CI workflow itself** — worth stating plainly, since finding them is exactly what this final gate is for:

1. CI's job env set `AUTH_CLIENT_ID`/`AUTH_CLIENT_SECRET` to values different from what `tests/conftest.py`'s `client` fixture hardcodes, which pre-empted the fixture's own fallback and made every authenticated test fail with 401 on every single CI run since H2 was introduced (invisible locally, since local shells never had those env vars pre-set). Fixed by no longer overriding them in CI — they aren't secrets, so there's no reason to diverge from local dev's values.
2. Fixing (1) surfaced one further failure: a new H6 test checked the `/health` Redis check against a real Redis, but CI had no Redis service. Fixed by adding a `redis:7-alpine` service container — which also means H4's real-Redis rate-limiter regression test now actually runs in CI instead of silently skipping.

Also corrected four comments/docstrings that cited fabricated finding codes (`DB1`, `DB2`, `OBS1`, `EP1` — none exist in the review) to the real ones (`D1`/`D4`, `P3`, `M1`/`M2`/`M3`, `E3`).

---

## 2. Verification results

All of the following were run for real in this session, not asserted:

- **Test suite:** 267 passed, 0 failed, 4 skipped (Redis/Postgres-dependent tests that clean-skip when those aren't available), 0 warnings beyond one pre-existing `httpx` deprecation notice. Started at 210 tests per the review's own count; +57 net new regression tests across H1-H8.
- **Migrations:** full `base → head → base → head` round-trip against real Postgres 16, `alembic check` reports zero drift, both before and after H5's migration `0008`.
- **Security scans:** `bandit -r src/content_factory -ll` — zero Medium/High findings (2 pre-existing Low findings in `template_pillow.py`, reviewed and accepted in the original review). `pip-audit -r requirements-lock.txt` — zero known vulnerabilities.
- **CI, for real, on GitHub Actions** (not just written and assumed correct): after the two H8 fixes above, run [`7b54cf5`](../../actions) completed **green** end to end — install, migrate, drift-check, round-trip, full test suite, `pip-audit`, `bandit`, **and a full container build via `docker build`**. The container-build step succeeding is significant: this sandbox's own egress policy blocks Docker Hub, so the `Dockerfile` from H1 was written and locally reviewed but never actually built here — CI building it successfully on a real runner is the independent verification this environment couldn't provide directly.
- **Real-infrastructure regression tests**, not mocks: Redis rate limiter proven against a real local Redis instance; budget governor's advisory lock proven with real threads against real Postgres (and confirmed to reproduce the race when the lock is removed); backup/restore scripts run end-to-end against real Postgres, including the safety guard rejecting an unforced second restore.

---

## 3. Remaining risks (explicit, not hidden)

Everything below is either an accepted tradeoff at current scale or a gap that needs a real external dependency (live platform credentials, a chosen cloud provider) this environment cannot supply — none of it is a design defect discovered late.

| Risk | Status | Recommendation |
|---|---|---|
| **E3** — TikTok/YouTube/Instagram never exercised against a live API | Plan written (H7), not executed — needs real app-review credentials that don't exist in this engagement. TikTok's and Instagram's providers skip a real polling step the live API likely requires; YouTube's sends metadata only, not actual video bytes, and will very likely need a real implementation before it can publish anything. | Follow `docs/PROVIDER_VERIFICATION_PLAN.md`'s sandbox checklist the moment credentials exist, before any live posting. |
| **P2** — N+1 `QualityScore` query in `list_videos`/`list_pending_review` | Not addressed this sprint (out of H5's stated scope of indexes/pagination). Negligible at today's volume; the pagination cap from H5 bounds its worst case per request. | Batch-load `QualityScore` for the page of video IDs in one query — small, well-scoped follow-up. |
| **S1**/**C2** — no rate limiting on cost-incurring endpoints independent of the dollar ceiling | Not addressed — H4 closed the *distributed* rate-limiting gap (S2) but did not add a *new* limit on research/script-gen/render/publish. The budget governor remains the only backstop, and it is now provably race-free (D2/C1 closed) but still per-dollar, not per-request-rate. | Add a coarse per-principal rate limit on these routes if/when the trusted-single-operator assumption changes. |
| **S3** — no CORS/security-header middleware | Not addressed — no frontend calls this API directly yet, so the risk is dormant. | Add before any browser-based frontend is introduced. |
| **S6** — `/docs`/`/redoc`/`/openapi.json` always public | Not addressed. | Gate behind auth or disable in production if the API surface shouldn't be publicly enumerable. |
| **M4** — no infrastructure-level alerting wired to a real pager | `docs/OBSERVABILITY.md` gives a concrete, prioritized alerting strategy; no Alertmanager/PagerDuty/Slack integration exists, since that requires choosing and configuring a real deployment target. | Wire `/health` polling and the Prometheus metrics from H6 into whatever monitoring stack the actual deployment target uses, per the documented priority order. |
| **M5** — `/health` doesn't check disk space | H6 added the Redis check M5 asked for; did not add a `MEDIA_STORAGE_DIR` disk-space threshold check. | Small follow-up if local-disk media storage remains in use. |
| **SC4**/**I6** (media half) — rendered media's primary store is still local disk | H3 added an S3 *backup* copy (off by default); this is explicitly not a migration of the primary read path to object storage. | Migrate to S3/R2 as primary storage before scaling beyond a single host, per `docs/DATABASE_OPERATIONS.md` §5's own framing. |
| **I3**/**I4** — no infrastructure-as-code, no enforced environment separation | Out of scope for this sprint (explicitly named as such in the original review); `docs/DEPLOYMENT.md` §7 documents the convention without enforcing it in tooling. | Write IaC and enforce environment separation before a second real environment (staging) is stood up. |
| Docker Compose stack never run end-to-end in this environment | The `Dockerfile` builds successfully (verified via CI, see §2); `docker-compose.yml`'s config merge was validated (`docker compose config`); but no session in this engagement could actually execute `docker compose up` and hit a live `/health` endpoint, since this sandbox has no Docker runtime access. | Run `docker compose up` once in any environment with a working Docker daemon and confirm `/health` returns 200 before the first real deploy — a fast, low-risk smoke test. |
| Whop's actual Content Rewards API | Unconfirmed since Phase 1 (`ARCHITECTURE.md`'s own open question) — outside this sprint's scope entirely. | Verify directly with Whop; unrelated to production-hardening. |

---

## 4. Recommendation

**READY.** Every P0 item (containerization, CI/CD, database safety) has been implemented and — critically — actually verified, including via a real green CI run with a successful container build, which is the one piece of independent verification this sandboxed environment could not provide on its own until GitHub Actions confirmed it. Every P1 item has real regression coverage, with two of the trickiest ones (the Redis rate limiter's distributed-safety property, the budget governor's concurrency fix) proven against real infrastructure rather than asserted from code review alone.

The remaining risks in §3 are exactly the kind of thing that should be visible and tracked, not blocking: none of them represents an unverified claim or a hidden defect, and none requires more than what's already documented to close.

**Update:** production hardening is approved. Before Phase 3, the system enters a Pilot Validation Phase — see `docs/PILOT_PLAN.md` — to prove measurable business results on real campaigns before any further architecture investment. Phase 3 remains gated behind that pilot's own decision criteria, not this report alone.
