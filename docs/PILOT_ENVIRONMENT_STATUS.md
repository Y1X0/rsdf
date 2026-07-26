# Pilot Environment Status

**Purpose:** a live, single-glance snapshot of whether the pilot is actually ready to start. This is not a plan (that's `docs/PILOT_PLAN.md`) and not an explainer of what's needed (that's `docs/PILOT_SETUP_CHECKLIST.md`) — it's the status board: what's done, who owns what's not, and whether any given gap is currently blocking.

**How to use this document:** update it as each item is actually completed — don't mark something `READY` until it's been verified, not just configured. Every row below reflects this environment's state as of the last time this document was written: nothing has been prepared yet, so every item is `NOT READY`. `Owner` fields say `Unassigned` because no one has been designated yet — fill them in as people are assigned.

**Current overall status: NOT READY. Pilot execution has not started and must not start until every mandatory item below is `READY`.**

---

## Status checklist

| Item | Status | Owner | Notes | Blocking Pilot? |
|---|---|---|---|---|
| 1. Anthropic API key | **NOT READY** | Unassigned | `ANTHROPIC_API_KEY` is empty in this environment; `resolved_llm_provider()` is currently falling back to `FakeLLMClient` (placeholder/empty content). Needs a real key set as an env var in the actual deployment target, then a real `POST /campaigns/{id}/research` call verified to return genuine, non-empty AI-generated content. See `docs/PILOT_SETUP_CHECKLIST.md` §1. | **Yes** |
| 2. Production Postgres environment | **NOT READY** | Unassigned | This environment's `DATABASE_URL` is a local/dev instance, not a real managed Postgres. Needs: a real managed instance provisioned, `DATABASE_URL` pointed at it, a backup actually taken and a restore actually tested *from that instance* (not assumed from prior hardening verification elsewhere). See `docs/PILOT_SETUP_CHECKLIST.md` §2, §6. | **Yes** |
| 3. Production environment variables | **NOT READY** | Unassigned | `ENVIRONMENT=production`, a real 32+ character `JWT_SECRET_KEY`, real (non-test) `AUTH_CLIENT_ID`/`AUTH_CLIENT_SECRET`, and `TOKEN_ENCRYPTION_KEY` (if any real platform OAuth token will be stored) all need real, freshly generated values in the actual deployment target — none of this environment's dev/test values may be reused. See `docs/PILOT_SETUP_CHECKLIST.md` §2, §8. | **Yes** |
| 4. Real renderer configuration | **NOT READY** | Unassigned | `RENDERER_BACKEND` is currently `null` (placeholder asset manifest, nothing playable). Needs `RENDERER_BACKEND=template_pillow` (or another real renderer) set and verified to produce an actual playable video file end to end via `POST /scripts/{id}/render`. `ELEVENLABS_API_KEY` is a separate, optional decision (silent voiceover is acceptable if the pilot's format tolerates it). See `docs/PILOT_SETUP_CHECKLIST.md` §1. | **Yes** |
| 5. Whop campaign access process | **NOT READY** | Unassigned | No confirmed public Whop API exists (`ARCHITECTURE.md`'s own open question) — this item is a *process* to confirm, not a credential to obtain. Needs: confirmation of how campaign briefs/terms are actually accessed, how submission/approval status is reported back, and how earnings data is actually obtained, plus at least 2-3 real, currently-open campaigns identified. See `docs/PILOT_SETUP_CHECKLIST.md` §3. | **Yes** |
| 6. Human reviewer/operator access | **NOT READY** | Unassigned | No named pilot reviewer is designated yet. Needs: a specific person (or small fixed rotation) assigned, issued real operator credentials (not shared test credentials), and committed to reviewing every pilot video with a `reason_code` on every non-approval. See `docs/PILOT_SETUP_CHECKLIST.md` §5. | **Yes** |
| 7. Publishing workflow/accounts | **NOT READY** | Unassigned | No creator account is registered yet on any platform. Minimum bar: at least one real creator account on at least one platform, with an explicit decision to publish manually (acceptable) or via live credentials (optional, better) — see `docs/PILOT_SETUP_CHECKLIST.md` §4, §10. Live TikTok/YouTube/Instagram API credentials are optional per platform; manual publishing to a real account is a real result, not a degraded one. | **Yes** (minimum: one account + a manual-or-automated decision) |
| 8. Metrics and revenue tracking setup | **NOT READY** | Unassigned | No `BudgetCeiling` has been set for the pilot; no decision made on manual cost-entry (reviewer time) or the cadence for entering real Whop revenue as it settles. Mechanically the endpoints already work (`POST /budget/ceilings`, `POST /videos/{id}/cost`, `POST /videos/{id}/revenue`) — what's missing is the pilot-specific ceiling value and the entry-cadence decision, not code. See `docs/PILOT_SETUP_CHECKLIST.md` §7. | **Yes** |

---

## Pilot Ready Criteria

**The pilot may start only when every item above is marked `READY`.** There is no partial-start condition — a pilot that begins with even one mandatory item unresolved (e.g., no real Anthropic key, or no assigned reviewer) cannot produce the real, measurable result `docs/PILOT_PLAN.md` exists to validate, and risks silently producing simulated or incomplete data that looks real in a report later.

Specifically, before flipping this document's overall status to READY:

- [ ] Items 1-6 and 8 are each independently verified `READY` — not just configured, *verified*: a real credential set is confirmed to actually work (a real API call succeeds), not just present in an env var.
- [ ] Item 7 is `READY` to at least its minimum bar: one real account exists and a manual-vs-automated publishing decision has been made and documented for it.
- [ ] Every `Owner` field above has a real, named assignee — an item cannot be `READY` while still `Unassigned`, since "ready" implies someone verified it, not that it happens to be true.
- [ ] This document has been updated to reflect the actual current state at the time the pilot starts — re-check every row immediately before day one, don't rely on a status set days or weeks earlier.

**Until all of the above hold, do not execute the pipeline, do not publish anything, and do not enter any metrics/revenue data framed as pilot results.** This environment specifically remains unable to produce real results (no real credentials configured here) regardless of this document's status — the pilot must run from the real deployment target described in `docs/PILOT_SETUP_CHECKLIST.md`, not from this development/CI environment.
