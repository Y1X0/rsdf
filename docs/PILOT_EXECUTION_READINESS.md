# Pilot Execution Readiness

**Purpose:** the single working document used *during* environment preparation and on pilot day one. `docs/PILOT_ENVIRONMENT_SETUP_GUIDE.md` explains how to obtain, configure, and verify each dependency; `docs/PILOT_ENVIRONMENT_STATUS.md` is the point-in-time status snapshot. This document is the checklist to actually work through, the day-one go/no-go gate, and the rollback/incident runbook for the pilot itself.

**Non-negotiables, restated because they govern every section below:**
- **Do not mark anything READY without recorded evidence.** Configuration alone is not readiness — see §3.
- **Do not use simulated data.** Every check below is either read-only or produces disposable, clearly-labeled verification data (e.g., `_setup_verification_delete_me`), never something entered or reported as if it were real pilot output.
- **Do not execute the pilot.** Nothing in this document is a step of the pilot itself — it is entirely preparation, verification, and the runbooks that will govern the pilot once it actually starts.
- **The pilot stays blocked** until every mandatory row in §2 is `READY`, with a named owner and recorded evidence, per `docs/PILOT_ENVIRONMENT_STATUS.md`'s own Pilot Ready Criteria.

---

## 1. Exact order of environment preparation

This is the sequence we will actually follow, matching the dependency order in `docs/PILOT_ENVIRONMENT_SETUP_GUIDE.md` §1 — that document has the full how-to for each step; this is the checklist of the order itself, to track against as we go:

1. [ ] Provision the real managed Postgres instance; enable automated backups + PITR at the provider level.
2. [ ] Generate every secret fresh (`JWT_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, `AUTH_CLIENT_SECRET`) and store them in the real deployment target's secret manager — before any of them are set as a live environment variable.
3. [ ] Set core environment variables (§4 of the setup guide) and deploy the application; confirm `GET /health` returns `200` before continuing.
4. [ ] Obtain and configure the Anthropic API key; verify real content generation (setup guide §6.3) before proceeding.
5. [ ] Configure the real renderer (`RENDERER_BACKEND=template_pillow`); verify a real playable video is produced (setup guide §6.4).
6. [ ] Confirm the Whop campaign access process firsthand; identify 2-3 real, currently-open campaigns.
7. [ ] Assign the named human reviewer; issue and verify their real operator credentials.
8. [ ] Register at least one real creator account; decide manual vs. automated publishing per platform.
9. [ ] Set the pilot's real budget ceiling; agree the cost/revenue entry cadence.
10. [ ] Run the full verification pass one more time, end to end, after every individual step above is done — not just once per step in isolation.
11. [ ] Update `docs/PILOT_ENVIRONMENT_STATUS.md` — flip each row to `READY` only as its own evidence (§3 below) is actually in hand.
12. [ ] Run the Day-One Pilot Checklist (§4) immediately before the pilot's first real action.

Do not skip ahead — step 9's budget ceiling is meaningless before step 4's real LLM key is verified (there's nothing real to meter yet), and step 12 is meaningless before step 11's status document is genuinely all-`READY`.

---

## 2. Checklist matching every `PILOT_ENVIRONMENT_STATUS.md` row

Work through each row below during preparation. This mirrors the status document's structure exactly — update both together, never one without the other.

| # | Item | Prep steps complete? | Verified (command run)? | Evidence recorded? | Owner assigned? | Row status |
|---|---|---|---|---|---|---|
| 1 | Anthropic API key | [ ] | [ ] | [ ] | [ ] | NOT READY |
| 2 | Production Postgres environment | [ ] | [ ] | [ ] | [ ] | NOT READY |
| 3 | Production environment variables | [ ] | [ ] | [ ] | [ ] | NOT READY |
| 4 | Real renderer configuration | [ ] | [ ] | [ ] | [ ] | NOT READY |
| 5 | Whop campaign access process | [ ] | [ ] | [ ] | [ ] | NOT READY |
| 6 | Human reviewer/operator access | [ ] | [ ] | [ ] | [ ] | NOT READY |
| 7 | Publishing workflow/accounts | [ ] | [ ] | [ ] | [ ] | NOT READY |
| 8 | Metrics and revenue tracking setup | [ ] | [ ] | [ ] | [ ] | NOT READY |

A row only moves to `READY` — here and in `docs/PILOT_ENVIRONMENT_STATUS.md` — when **all four** of its columns are checked, not when the underlying config exists. "I set the environment variable" is not the same claim as "I verified it works and wrote down the proof."

---

## 3. Required evidence for each READY status

Restated here as the acceptance bar for §2's table (full verification commands are in `docs/PILOT_ENVIRONMENT_SETUP_GUIDE.md` §6-§7 — this is the checklist of *what evidence must exist*, not how to produce it):

| # | Item | Evidence required |
|---|---|---|
| 1 | Anthropic API key | The real research-call response body (or a clear excerpt) showing genuine generated text, not the empty/canned fallback shape — with timestamp and who ran it. |
| 2 | Production Postgres environment | Backup archive filename/timestamp; restore drill's row-count confirmation output; a `/health` response showing `database: ok`. |
| 3 | Production environment variables | Confirmation the app is running under `ENVIRONMENT=production`; the `/health` response; a successful real-reviewer token issuance (confirms `AUTH_CLIENT_ID`/`SECRET` are real, not test values). |
| 4 | Real renderer configuration | A link to or copy of an actual rendered, playable video file; the API response showing `render_status: "completed"`. |
| 5 | Whop campaign access process | A written note naming how access works, who has it, and the 2-3 real campaigns identified by name — timestamped, with who confirmed it. |
| 6 | Human reviewer/operator access | The real reviewer's successful token issuance and review-submission output, under their actual identity — plus their name recorded as owner. |
| 7 | Publishing workflow/accounts | The resulting `Publication` row (automated) or the manual-post confirmation, plus the documented per-platform manual-vs-automated decision. |
| 8 | Metrics and revenue tracking setup | The fail-closed `402` output at a test ceiling, the ceiling reset to its real pilot value, and the written cost/revenue entry-cadence decision. |

**No row moves to READY on the strength of a description of intended evidence — the evidence itself (the actual command output, the actual file, the actual written confirmation) must be attached or linked in `docs/PILOT_ENVIRONMENT_STATUS.md`'s Notes column.**

---

## 4. Final day-one pilot checklist

Run this immediately before the pilot's first real action (the first real research call against a real campaign) — not the morning before, not "close enough," but immediately prior:

- [ ] Every row in §2 reads `READY`, with all four columns checked, re-confirmed today (not relying on a status set days or weeks earlier — re-verify anything time-sensitive, especially credentials and account health).
- [ ] `GET /health` returns `200` with every configured dependency `ok`, checked right now.
- [ ] The real budget ceiling is set to its intended pilot value (not a leftover test value from verification).
- [ ] The named human reviewer confirms their availability for the pilot's actual window, not just that their credentials work.
- [ ] Every creator account intended for this pilot shows `health_tier: healthy` right now (`GET /accounts`) — a tier drop between setup and day one is a real, current signal, not stale data.
- [ ] The 2-3 real campaigns for this pilot are confirmed still open/active with Whop today.
- [ ] Rollback levers (§5) are confirmed to still work — re-run the `PUBLISHING_ENABLED` and budget-ceiling tests from the setup guide one final time if any material time has passed since they were last tested.
- [ ] Whoever is on call for the pilot's monitoring/alerts (§6) is identified and actually available for the pilot's duration, not just "someone will probably see it."
- [ ] A explicit written go/no-go decision is recorded (who made it, when) before the first real action — a pilot that "just sort of started" is exactly the kind of ambiguity this whole process exists to avoid.

If any single item above fails on the day: **do not start.** Fix it, or explicitly reschedule — do not proceed on the assumption that a failing item is "probably fine."

---

## 5. Rollback checklist

Consolidates the rollback levers from `docs/PILOT_PLAN.md` §3 into one checklist to use if something needs to stop during the pilot itself:

- [ ] **Stop new publishing immediately:** set `PUBLISHING_ENABLED=false`. No redeploy required — takes effect on the next request. Confirm with a real check (`POST /videos/{id}/publish` now returns `503`) rather than assuming the flag took effect.
- [ ] **Stop new spend immediately:** lower the active `BudgetCeiling` to at or below current spend (`POST /budget/ceilings`). The fail-closed governor blocks all further cost-incurring requests at the next check — no code change, no redeploy.
- [ ] **Remove a bad live publication:** there is no automated "unpublish" in this system — this is a manual action taken directly on the platform (TikTok/YouTube/Instagram/wherever it was posted). After removing it there, update the corresponding `Publication.status` to reflect reality (currently requires direct database access — a known, accepted gap, not something to build mid-incident).
- [ ] **Database issue (corruption, bad write):** restore from the most recent verified backup (`scripts/restore_postgres.sh`) rather than attempting a live fix — this is exactly what the restore drill in §3/item 2 proved works. Do **not** reach for `alembic downgrade` unless the issue is specifically a schema problem (unlikely, since no schema changes are planned during the pilot).
- [ ] **Full stop:** pause the pilot entirely (`PUBLISHING_ENABLED=false` *and* the budget ceiling dropped) if the issue's scope is unclear — resume only after the incident response process below has produced a clear understanding of what happened.

Every rollback action taken must be logged: what was changed, when, by whom, and why — this becomes part of the pilot's own record and, if it happens, part of §6's incident writeup.

---

## 6. Incident response checklist

For anything during the pilot that isn't routine — a policy violation posted live, a cost spike, a data integrity concern, a platform account getting flagged/restricted, or anything else that makes someone stop and say "is this okay?":

1. [ ] **Pause first, diagnose second.** Apply the relevant rollback lever(s) from §5 immediately — a paused pilot that turns out to have been a false alarm costs a delay; an unpaused real incident compounds every additional minute it's live.
2. [ ] **Notify the reviewer/owner and anyone externally affected.** If a live post is the issue, notify the campaign's advertiser/Whop contact promptly — don't wait for a full internal diagnosis before telling the people who need to know something happened.
3. [ ] **Establish what actually happened**, using the system's own record: `agent_run` entries, `ReviewDecision` history, `Publication` status/timestamps, structured logs correlated by `X-Request-ID` (see `docs/OBSERVABILITY.md` §3), and Sentry if configured. Do not rely on memory or assumption — the audit trail exists specifically for this.
4. [ ] **Classify the incident:** a content/policy problem (a human review miss), a cost problem (a spend spike), a platform problem (account restriction, API failure), or a system problem (an actual bug). The classification determines whether this is a "fix the process" issue or a "file a bug, possibly notify whoever owns this codebase" issue — don't assume the system is at fault before checking.
5. [ ] **Decide: resume, adjust, or stop the pilot.** Resuming requires the specific cause to be understood and addressed (not just "it seems fine now"). Adjusting means changing something narrow (e.g., tightening the quality gate, pausing one specific account) and resuming everything else. Stopping the pilot entirely is appropriate if the incident calls the pilot's basic safety into question, not just one campaign or account.
6. [ ] **Write it up** — a short, factual record: what happened, when it was detected, what was done, what the root cause was, and what changes (if any) follow from it. This feeds directly into `docs/PILOT_RESULTS_REPORT.md`'s eventual failure-analysis section (per `docs/PILOT_PLAN.md` §2's original plan) — don't let incident detail get lost between when it happens and when the pilot's results are eventually written up.
7. [ ] **Re-run the relevant §3 verification** before resuming anything the incident touched — e.g., if `PUBLISHING_ENABLED` was flipped off and back on, re-confirm publishing actually works again before trusting it silently does.

---

**Current status: environment preparation not started. Every row in §2 is `NOT READY`. Waiting for environment setup to be completed and recorded before any further action.**
