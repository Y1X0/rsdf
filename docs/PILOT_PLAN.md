# Pilot Validation Plan

**Status:** Production Hardening Sprint approved and recommends READY (`docs/PRODUCTION_HARDENING_REPORT.md`). Phase 3 (further automation/architecture investment) does not start until this pilot produces the data in §4 and clears the decision thresholds in §5.

**Purpose:** prove the system produces a measurable business result — approved videos, real views, real profit — end to end, on real Whop campaigns, with a human in the loop at every irreversible step. This is not a build phase: every capability used below already exists (campaigns, research, ideas, scripts, render, review, publish, metrics, profit rollups). Nothing in this document requires new code.

---

## 1. Real-world test workflow

### 1.1 Scope of the pilot

- **Campaigns:** 2-3 real Whop campaigns, chosen for having an actual advertiser brief and a real payout model already available (`POST /campaigns` — `brand_name`, `niche_name`, `cpm_rate` or `payout_model`, `budget_cap`, `rules_text`). Do not use placeholder/test campaigns — the goal is a real business result, not a pipeline smoke test (that's already covered by the automated test suite).
- **Volume ceiling:** a controlled, small batch — **10-15 videos total** across all campaigns, not per campaign. This is deliberately below the ~15-20/month steady-state volume ARCHITECTURE.md scopes the system for: the pilot needs to be small enough that every video gets genuine human attention in review, and small enough that a bad pattern (a weak hook, a mis-scored niche) doesn't compound before anyone notices.
- **Duration:** run the pilot over 2-4 weeks, not a single day — this is the minimum window in which `MetricsSubmitRequest`/analytics-sync data (views, completion rate, rewatch rate) becomes meaningful; platform view counts on day one are not a signal, they're noise.
- **Budget ceiling:** set a real, tight `BudgetCeiling` (`POST /budget/ceilings`, `scope: "system"` or scoped to the pilot's niche) before producing anything — sized to the 10-15-video ceiling, so the existing fail-closed budget governor (`enforce_budget`, 402 at 100%) is the actual backstop that keeps the pilot from silently overspending, not a manual watch.

### 1.2 Step-by-step workflow (using the existing pipeline as-is)

1. **Campaign setup** — `POST /campaigns` for each selected Whop campaign; `POST /campaigns/{id}/score` to get the Campaign Intelligence composite score before committing to it, so the pilot isn't spent on a campaign the system itself would flag as weak.
2. **Research** — `POST /campaigns/{id}/research` with real notes about the campaign/competitor landscape; review the resulting `ResearchBrief` and the hooks/patterns it seeds (`GET /hooks`, `GET /patterns`) before generating ideas from it.
3. **Ideation** — `POST /campaigns/{id}/ideas`, one or a few per campaign. A human picks which ideas actually proceed to scripting — this is the first human gate, and it's cheap to exercise (no cost incurred yet).
4. **Script generation** — `POST /ideas/{id}/scripts` (budget-gated already). Generate 2 variants per idea by default (the system's existing A/B convention via `experiment_group`), not more — more variants per idea spends pilot budget without adding pilot signal at this scale.
5. **Render** — `POST /scripts/{id}/render` (budget- and quality-gated already: automated QC + the four quality scores are computed here, and the quality gate auto-rejects if thresholds are configured — see 1.3).
6. **Human review (the second, mandatory gate)** — see 1.3 below in full; nothing in this pilot publishes without an explicit `approved` decision from a human reviewer.
7. **Publish** — `POST /videos/{id}/publish` against a real, already-warmed-up creator account (`GET /accounts` — must be `health_tier: healthy` and past its warmup window; the existing cadence-cap and account-health gating in `publishing_service.py` applies unchanged and should not be bypassed for pilot speed).
8. **Measure** — see 1.4 and §2 below.

### 1.3 Human review process

Every video produced in the pilot goes through `GET /videos/pending-review` → a human reviewer → `POST /videos/{id}/review`, with no exceptions:

- **Reviewer identity:** a named person, not "whoever is around" — the API already binds the decision to the authenticated principal (`auth/routers/review.py` uses the JWT subject, not a client-supplied name), so use one real, consistent operator token for pilot reviews so the reviewer-attribution data is clean.
- **Decision set:** `approved` / `rejected` / `revision_requested`, exactly as today — no new decision states for the pilot.
- **Reason codes on every rejection/revision:** required, not optional, for the pilot specifically — this is what feeds `content_intelligence.record_review_pattern`'s known-bad-pattern detection and is exactly the raw material §4 needs. A rejection with no reason code is pilot data lost.
- **What a reviewer is checking:** brand-safety/policy fit against the real campaign's `rules_text`, AI-disclosure correctness (`contains_ai_voice`/`contains_ai_visual` propagation into the publish caption — verify it actually shows up, don't just trust the field), and a basic quality bar the automated QC/quality scores don't fully capture (does the hook actually land, does the pacing feel right) — the human is the check on exactly what the quality scores are *informational*, not gating, about by default.
- **Quality-gate configuration decision (make this explicit before the pilot starts, don't leave it at whatever the last environment happened to have):** either leave `QUALITY_ORIGINALITY_AUTO_REJECT_FLOOR`/`QUALITY_POLICY_RISK_AUTO_REJECT_CEILING` at their disabled defaults (every video reaches human review, maximum data) or set them deliberately (fewer videos reach human review, but the ones that do are pre-filtered) — pick one and record which, since it changes how §4's "content patterns" data should be interpreted.

### 1.4 Publishing process

- Publish only to accounts already past warmup (`AccountWarmupStatus.ACTIVE`) with `health_tier: healthy` — the pilot is not the place to also be testing new-account warmup dynamics; that's a confound on the content-quality signal the pilot exists to measure.
- Respect each account's real `daily_post_cap` — do not raise it for pilot convenience.
- Record `scheduled_at` deliberately (don't leave it at "right now" for every video) so §4's posting-time data point is real, not degenerate.
- If publishing to a platform whose provider is still `ManualPublishingProvider` (no live credentials yet, per `docs/PROVIDER_VERIFICATION_PLAN.md`) — that's fine for the pilot: publish manually per that plan's process, then record the outcome the same way (external post ID, published-at time) so the measurement step in §1.5 is uniform regardless of which platforms have live automation yet.

### 1.5 Measurement process

- **Sync cadence:** for automated platforms, `POST /publications/{id}/metrics/sync`; for manual platforms, enter metrics via the existing `POST /videos/{id}/metrics` endpoint using the same field set (`views`, `avg_watch_time_s`, `completion_rate`, `rewatch_rate`, `shares`, `comments`, `likes`, `saves`) so both paths produce identical downstream data. Sync at consistent checkpoints — **24h, 72h, and 7 days** after publish, minimum — not once, arbitrarily.
- **Cost capture:** the system already records this automatically via `agent_run()`'s cost recorder for every AI-incurring step, plus `POST /videos/{id}/cost` for anything manual (e.g., a human's review time, if you choose to track it in dollars). Don't skip manual cost entry — `compute_profit_summary`'s cost side is only as complete as what's actually recorded.
- **Revenue capture:** `POST /videos/{id}/revenue` as real payout data becomes available from Whop/the platform — this will lag views by the campaign's own payout cadence; don't force a premature revenue number just to close out a report early.
- **Profit rollup:** `GET /videos/{id}/profit`, `GET /niches/{id}/profit`, `GET /accounts/{id}/profit` — all three already exist and require no new code; pull all three at the end of the pilot window, not just per-video.

---

## 2. Success metrics

Every metric below maps to a field or endpoint that already exists — nothing here requires new instrumentation.

| Metric | Source | Notes |
|---|---|---|
| **Videos produced** | Count of `Video` rows created during the pilot window (`GET /videos`, paginated) | Track against the 10-15 ceiling in §1.1 — going over it without an explicit decision to extend the pilot is itself a signal the estimate was wrong. |
| **Approval rate** | `ReviewDecision.decision` distribution (`approved` / `rejected` / `revision_requested`) per `POST /videos/{id}/review` | Track separately from any quality-gate auto-rejections (`reviewer_id="system:quality_gate"`) if that gate is enabled — conflating human and automated rejections hides which one is actually filtering. |
| **Views** | `MetricsSnapshot.views` via metrics sync/manual entry | Per checkpoint (24h/72h/7d), not just a final number — the trajectory matters more than the endpoint for later hook/niche analysis. |
| **Retention** | `avg_watch_time_s`, `completion_rate`, `rewatch_rate` | `QualityScore.retention_prediction_score` (pre-publish prediction) vs. actual `completion_rate` (post-publish reality) is a specific, valuable comparison — it tells you whether the retention *prediction* is worth trusting going into Phase 3. |
| **Engagement** | `shares`, `comments`, `likes`, `saves`, and the derived `ViralScoreRecord.score`/`recommendation` | The viral score already exists per video (`GET /videos/{id}` → quality/viral data via the metrics response) — use it, don't recompute engagement by hand. |
| **Revenue** | `RevenueSnapshot` via `POST /videos/{id}/revenue` | Real payout data only — do not estimate/backfill from CPM assumptions for the pilot's own numbers; estimation is fine for planning, not for the pilot's actual result. |
| **Production cost** | `CostLedger` via `agent_run()` auto-recording + manual entries | Break down by category (`llm`/`tts`/`render`/`human_review`/`other`) — the category split is what tells you where pilot-scale cost actually goes, which matters directly for §5's "invest in more AI generation tools" threshold. |
| **Profit per video** | `GET /videos/{id}/profit` | Pull once metrics/revenue have stabilized (7-day checkpoint), not immediately after publish. |
| **Profit per campaign** | Sum of per-video profit within a campaign, or `GET /niches/{id}/profit` if the pilot's campaigns map cleanly to one niche each | If a niche spans multiple pilot campaigns, compute per-campaign manually from the video-level data — there is no per-campaign rollup endpoint today, and adding one is out of scope for this document (pilot plan, not a feature request). |

Record all of the above in one place per video (a simple tracking sheet is enough — this document does not prescribe a new dashboard) so §4's cross-cutting analysis (hook vs. niche vs. cost) can actually be done at the end of the window.

---

## 3. Operational checklist

Run this in full before producing a single pilot video, and re-check the first four items on every subsequent day of the pilot window.

**Deployment verification**
- [ ] Confirm which environment the pilot runs in and that `ENVIRONMENT=production` is set there if it's meant to be a real deployment — `Settings.validate_production_safety()` will refuse to start otherwise if `DATABASE_URL` is SQLite or `JWT_SECRET_KEY` is missing/short, which is the correct, intended behavior, not a bug to work around.
- [ ] `GET /health` returns `200` with every configured dependency (`database`, and `redis` if `RATE_LIMIT_BACKEND=redis`) reporting `"ok"`.
- [ ] If running via Docker: confirm the image actually builds and `docker compose up` brings up a working stack in *this* environment specifically — the production hardening report flagged that this was verified via CI but never run end-to-end in every environment; don't assume it carries over untested.

**Backups verified**
- [ ] A real backup has been taken (`scripts/backup_postgres.sh` or the managed-service equivalent) *before* the pilot's first write, and its restore path has been tested at least once in this environment specifically — not just trusted from the H3 verification done during hardening.
- [ ] Backup retention policy (`docs/DATABASE_OPERATIONS.md` §4) is actually configured, not just documented, for the environment the pilot runs in.
- [ ] If `MEDIA_BACKUP_ENABLED=true` is desired for pilot assets: confirm the S3 bucket/credentials are actually configured and a test upload succeeds, before relying on it.

**Monitoring active**
- [ ] `GET /metrics` is being scraped by something (Prometheus or equivalent) — not just present and unwatched.
- [ ] `SENTRY_DSN` is set if error tracking is wanted for the pilot window — errors during a real pilot are exactly the kind of thing worth aggregating, not just grepping logs for after the fact.
- [ ] Someone is actually watching `/health` and the budget governor's alert notifications (`NotificationProvider` — Slack/email, not just the log default) during the pilot window — `docs/OBSERVABILITY.md`'s alerting strategy is a recommendation, not a running system, until a human or a real alerting pipeline is on the other end of it.

**Provider credentials checklist**
- [ ] For each platform the pilot will actually publish to: either real, verified credentials exist (per `docs/PROVIDER_VERIFICATION_PLAN.md`'s sandbox checklist, completed) or the plan is to publish manually via `ManualPublishingProvider` for that platform — decide this per platform *before* the pilot starts, not per video.
- [ ] `TOKEN_ENCRYPTION_KEY` is set if any real OAuth token will be stored (`POST /accounts` with `oauth_token`) — publishing will fail closed with a clear 500 otherwise, which is correct, but better to know in advance than discover it mid-pilot.
- [ ] `PUBLISHING_ENABLED=true` (or the pilot's chosen value) is confirmed, and someone knows how to flip it to `false` immediately if needed (see rollback, below).

**Rollback procedure**
- [ ] **Stop new production immediately:** set `PUBLISHING_ENABLED=false` (kill-switch, no redeploy needed) to halt further publishing while leaving research/scripting/review unaffected, if the issue is publishing-specific.
- [ ] **Stop new spend immediately:** lower the pilot's `BudgetCeiling` to at or below current spend (`POST /budget/ceilings`) — the fail-closed governor will then block all further cost-incurring requests at the next check, with no code change.
- [ ] **Remove a bad publication:** there is no automated "unpublish" — removing a live post is a manual action on the platform itself; update `Publication.status` to reflect reality afterward (currently requires direct DB access or a small follow-up endpoint — not something to build mid-pilot, just know this gap exists going in).
- [ ] **Database rollback:** `alembic downgrade <revision>` only if a *schema* change is at fault (unlikely mid-pilot, since no schema changes are planned for this phase) — restoring from the verified backup is the correct response to data corruption, not a migration downgrade.
- [ ] **Full stop:** if something is seriously wrong (a policy violation posted live, a runaway cost), the actual first action is human: pause the pilot, notify the campaign's advertiser contact if a live post is affected, and only then work through the technical rollback steps above in whatever order the specific incident requires.

---

## 4. Data collection plan

**Principle: no optimization, no threshold-tuning, no "let's just tweak the prompt" changes during the pilot window itself.** The entire point of a pilot is to collect a clean, unoptimized baseline; changing the system mid-pilot in response to early results contaminates the very data this phase exists to produce. Resist the temptation — write down what you'd want to change, and change it *after* the window closes, informed by the full dataset, not the first three data points.

Collect, per video, at minimum:

- **Winning hooks:** hook text, `hook_type`, `HookSource` (own-generated vs. `competitor_observed`), and its resulting `ViralScoreRecord.score` — already computed and stored in `hook_library`/`viral_scores`; pull it, don't re-derive it. A hook "wins" for pilot purposes if its videos land in the top half of the pilot's own viral-score distribution — a relative, pilot-local bar, not an absolute one borrowed from someone else's benchmark.
- **Winning niches:** per-niche aggregate profit and per-niche average viral score (`GET /niches/{id}/profit` + the niche's videos' scores) — the pilot's 2-3 campaigns should span at least 2 different niches specifically so this comparison is possible at all; running the whole pilot in one niche produces no niche-comparison data.
- **Audience response:** the full engagement/retention field set from §2, plus qualitative signal review missed — comments' actual content (not just count) is worth a human skim for pattern-spotting that a count alone won't surface, even though this system doesn't have a comment-sentiment feature (correctly out of scope to build now).
- **Content patterns:** `LearningPattern` rows already created via `record_review_pattern`'s known-bad-pattern detection (repeated rejection reasons) and via the Experimentation Engine if run in recommend-only mode (`POST /experimentation/run`, `axis: hook|niche|length|posting_time` — running this against pilot data is itself part of data collection, not optimization, since it only ever produces recommendations, never applies them: `POST /experimentation/recommendations/{id}/apply` should **not** be called during the pilot).
- **Cost efficiency:** cost-per-video and cost-per-approved-video (the second is the more honest number — it accounts for scripts/videos that got rejected and produced zero business value), broken down by cost category (§2) — this is the single most important number feeding §5's automation-investment threshold.

At the end of the pilot window, produce one consolidated dataset (video-level rows with every field above) before making any Phase 3 decision — the decision in §5 should be made by looking at this dataset, not by impression.

---

## 5. Phase 3 decision criteria

Set explicit thresholds *before* the pilot ends — deciding what "good" means after seeing the numbers is how a team talks itself into scaling a mediocre result. The numbers below are starting points to fill in with the pilot's own budget and campaign economics; the structure (a clear yes/no per axis, tied to a specific number from §2/§4) is the actual requirement, not these exact figures.

| Decision | Proceed if... | Hold / don't proceed if... |
|---|---|---|
| **Scale automation** (e.g., raise auto-reject thresholds, enable more auto-gating) | Approval rate ≥ a pre-agreed floor (e.g., 60%+) **and** the quality/viral scores' predictions correlate with actual engagement well enough that trusting them further is defensible — check this explicitly (§2's retention-prediction-vs-actual comparison), don't assume it. | Approval rate is low because of a *systemic* pattern (not just campaign-specific taste) — automating around a systemic problem compounds it. |
| **Add more accounts** | At least one account finished the pilot at `health_tier: healthy` with profit-per-video ≥ 0 (or ≥ whatever margin the business requires) **and** cadence-cap gating never had to be manually overridden to hit the pilot's volume. | Any account dropped tier during the pilot, or cadence caps were binding constraints (a real signal that current caps, not more accounts, are the limiter). |
| **Increase content volume** | Cost-per-approved-video and profit-per-video are both stable or improving across the pilot window (not just positive once) **and** review capacity (the human reviewer's actual throughput) has headroom above the pilot's volume. | Profit-per-video is negative, inconsistent, or review is already the bottleneck at pilot scale — more volume through a already-saturated human gate degrades review quality, which degrades everything downstream. |
| **Invest in more AI generation tools** (real ElevenLabs/template-rendering credentials, a paid renderer, generative video, etc.) | Cost breakdown (§2) shows a *specific* stage (TTS, rendering, LLM) as the dominant cost **and** the pilot's content quality was good enough that the bottleneck is clearly capability, not judgment — investing in better tools to execute a strategy that isn't working yet is wasted spend. | The pilot's main losses were from rejected/low-performing content, not from a tooling limitation — that's a niche/hook/creative problem, not a tooling problem, and no tool purchase fixes it. |

**Minimum bar to even hold this decision meeting:** all four checklist sections in §3 were genuinely followed (not retroactively checked off), and the §4 dataset is complete for every pilot video — a decision made on partial data is worse than waiting one more week for complete data.
