# Pilot First-Run Execution Checklist

**Purpose:** the narrow, prioritized checklist for the *first* real execution — one real campaign through the pipeline to one measured video — before committing to the full 10-15-video pilot `docs/PILOT_PLAN.md` describes. Think of this as the single smoke test that proves the system works end to end in reality, not simulation, before scaling up to the fuller pilot volume.

**Scope discipline:** this document adds no features and redesigns nothing — every step below calls an endpoint, script, or check that already exists. If any step reveals something is actually missing, stop and report it; do not build it here.

**Preconditions — do not start §1 until these are true:**
- `docs/PILOT_ENVIRONMENT_STATUS.md` shows real progress on at minimum the items this checklist depends on (a real Anthropic key, a real deployment target) — this checklist does not replace that document, it executes against what it tracks.
- Whoever runs this checklist has real operator credentials (not shared test values) and knows the deployment target's real base URL.

---

## Priority 1 — Verify Anthropic API in a real environment

Do this first: everything downstream is worthless if content generation isn't real.

1. [ ] Confirm `ANTHROPIC_API_KEY` is set in the **real deployment target's** environment (not this development sandbox) — via whatever secret mechanism `docs/PILOT_ENVIRONMENT_SETUP_GUIDE.md` §5 specifies.
2. [ ] Confirm `ANTHROPIC_MODEL`/`ANTHROPIC_MODEL_VERSION` match a model the key's account actually has access to.
3. [ ] Restart/redeploy the application so the new key is actually loaded (env vars are read at process start via `Settings`/`@lru_cache`).
4. [ ] Issue a real operator token: `POST /auth/token` with real `AUTH_CLIENT_ID`/`AUTH_CLIENT_SECRET`.
5. [ ] Create one throwaway, clearly-named campaign: `POST /campaigns` with `brand_name: "_pilot_verification_delete_me"`.
6. [ ] Call `POST /campaigns/{id}/research` with real notes and inspect the response's `structured_data` directly.
7. [ ] **Pass criterion:** the response contains genuine, specific, non-empty generated text (a real `brief_text`, real `competitor_hooks` with actual content) — not the empty/canned shape `FakeLLMClient` would produce. If it looks canned, the key isn't actually being used; check `resolved_llm_provider()`'s fallback condition before proceeding.
8. [ ] Record the evidence (response body, timestamp) in `docs/PILOT_ENVIRONMENT_STATUS.md`'s row 1 and flip it to `READY`.
9. [ ] Delete or clearly retain the throwaway campaign as verification-only — do not let it contaminate real pilot data later.

**Do not proceed to Priority 2 until step 7 passes.**

---

## Priority 2 — Deploy the application

1. [ ] Confirm the real Postgres instance is provisioned and reachable; `alembic upgrade head` (or `docker run <image> migrate`) has been run against it to completion.
2. [ ] Confirm every mandatory environment variable from `docs/PILOT_ENVIRONMENT_SETUP_GUIDE.md` §3 is set for real (not left at a dev/test default) — `ENVIRONMENT=production`, real `JWT_SECRET_KEY`/`AUTH_CLIENT_ID`/`AUTH_CLIENT_SECRET`, real `DATABASE_URL`, `RENDERER_BACKEND=template_pillow`.
3. [ ] Build and deploy the container to the real target (`docker build`, then `serve`, per `docker-entrypoint.sh`'s migrate-then-serve sequence — migration already done in step 1, so this is the `serve` variant only).
4. [ ] `curl <real-host>/health` — confirm `200` with every configured dependency (`database`, and `redis` if configured) reporting `ok`.
5. [ ] Confirm the app actually started under `ENVIRONMENT=production` (check the platform's own logs/config view) — this also implicitly re-confirms `Settings.validate_production_safety()` didn't refuse to start, which it would have if `DATABASE_URL`/`JWT_SECRET_KEY` were wrong.
6. [ ] Set a real `BudgetCeiling` (`POST /budget/ceilings`) sized for this first-run smoke test (a small, specific dollar amount — this is one campaign's worth of research/script/render, not the full pilot's budget).
7. [ ] Confirm the rollback levers work in this real deployment: flip `PUBLISHING_ENABLED=false`, confirm `POST /videos/{id}/publish` returns `503`, flip it back to `true`.
8. [ ] Record evidence and flip the relevant rows (`docs/PILOT_ENVIRONMENT_STATUS.md` items 2 and 3) to `READY`.

**Do not proceed to Priority 3 until `/health` returns 200 from the real deployment and the budget ceiling is set.**

---

## Priority 3 — Run one complete real content pipeline

One real Whop campaign, through the actual pipeline, for the first time, in the real environment. Per `docs/PILOT_SETUP_CHECKLIST.md` §3, Whop campaign data entry is manual — that's expected, not a gap to fix here.

1. [ ] Identify one real, currently-open Whop campaign (name, brand, `rules_text`, payout model) — confirmed firsthand, not assumed.
2. [ ] `POST /campaigns` with the real campaign's real data.
3. [ ] `POST /campaigns/{id}/score` — review the Campaign Intelligence composite score before committing further effort; if it scores poorly, decide explicitly whether to continue with this campaign or pick a different real one now, before spending more.
4. [ ] `POST /campaigns/{id}/research` with real notes about the campaign/competitor landscape (already verified working in Priority 1 — this run is real pilot data, not a throwaway).
5. [ ] Review the resulting `ResearchBrief` and the hooks/patterns it seeded (`GET /hooks`, `GET /patterns`) — a human reads this before proceeding, not just checks it returned `200`.
6. [ ] `POST /campaigns/{id}/ideas` — generate one or a small number of ideas.
7. [ ] **Human gate 1:** a person picks which idea(s) actually proceed to scripting. Record which were picked and why, and which were rejected and why — this is real data for later analysis, not paperwork.
8. [ ] `POST /ideas/{id}/scripts` — generate script variants (2 is the established default) for the selected idea.
9. [ ] Confirm the budget ceiling from Priority 2 is still not exceeded (`GET /budget/status`) — if it's close, decide explicitly whether to continue or raise it, don't just let it silently block mid-pipeline.

**Do not proceed to Priority 4 without a human having explicitly chosen which idea/script to render** — this is the mandatory gate, not a formality.

---

## Priority 4 — Produce the first publishable video

1. [ ] `POST /scripts/{id}/render` for the chosen script.
2. [ ] Confirm `render_status: "completed"` and `Video.asset_url` points at a real, playable file — open it and actually watch it.
3. [ ] Confirm the automated QC/quality scores are present (`GET /videos/{id}` → `quality_score`) and read them, even though (per this pilot's own quality-gate decision, `docs/PILOT_PLAN.md` §1.3) they're informational unless deliberately configured otherwise.
4. [ ] **Human gate 2 (mandatory, no exceptions):** the named pilot reviewer reviews the actual video against the real campaign's brand-safety/policy requirements and AI-disclosure correctness. `POST /videos/{id}/review` with a real decision.
   - If `rejected`/`revision_requested`: record the real `reason_code` — this is the first real content-pattern data point, not a discard.
   - If `approved`: continue.
5. [ ] Confirm at least one real creator account is registered (`GET /accounts`) with `health_tier: healthy`, and the manual-vs-automated publishing decision for it is already made (per `docs/PILOT_SETUP_CHECKLIST.md` §10 — manual is a fully legitimate first-run choice).
6. [ ] Publish: `POST /videos/{id}/publish` (automated) or post it manually to the real account, then record the outcome the same way (`external_post_id` if automated, or the manual post's real URL/ID) so downstream measurement is uniform.
7. [ ] Confirm `GET /publications` shows the resulting row.

**This is the first publishable video.** If it was rejected in step 4, this checklist's Priority 4 repeats from step 1 with a new script/idea — a rejection is a normal outcome of a real review gate, not a checklist failure.

---

## Priority 5 — Measure the first results

1. [ ] At the 24h checkpoint: sync or manually enter metrics (`POST /publications/{id}/metrics/sync` if automated, else `POST /videos/{id}/metrics` with the same field set — views, `avg_watch_time_s`, `completion_rate`, `rewatch_rate`, shares, comments, likes, saves).
2. [ ] Repeat at 72h and 7 days — a single reading is not a trend; don't draw conclusions from the 24h number alone.
3. [ ] Enter real production cost if anything beyond the auto-recorded `agent_run()` cost applies (e.g., reviewer time) via `POST /videos/{id}/cost`.
4. [ ] Enter real revenue via `POST /videos/{id}/revenue` as actual Whop payout data becomes available — do not estimate or backfill a placeholder number to "complete" this step early.
5. [ ] Pull `GET /videos/{id}/profit` once metrics/revenue have had time to settle (the 7-day checkpoint, not immediately after publish).
6. [ ] Record everything from this one video — hook used, niche, review outcome and reason (if any), views/retention/engagement at each checkpoint, cost, revenue, profit — in one place, matching the fields `docs/PILOT_PLAN.md` §2/§4 already define, so this first data point slots directly into the fuller pilot's eventual dataset rather than needing to be reformatted later.
7. [ ] **This is the first real, measured result.** Use it to sanity-check the rest of this checklist itself (did every step work as documented?) before repeating Priorities 3-5 for the remaining pilot volume.

---

## After the first run

Do not treat one video's numbers as a verdict on anything — one data point proves the pipeline works end to end in reality, which is this checklist's entire purpose. Proceed to the fuller `docs/PILOT_PLAN.md` volume (10-15 videos, 2-4 weeks) using the same steps above, repeated, before consulting `docs/PILOT_PLAN.md` §5's actual Phase 3 decision criteria — those thresholds need real sample size behind them, not one video.
