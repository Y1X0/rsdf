# AI Content Factory — Architecture & Strategy Document

**System:** Automated content pipeline for Whop Content Rewards campaigns
**Status:** This is the original pre-implementation design document (v0.3)
and is kept as-written for historical reference — it still describes the
full three-phase design intent. **Phase 1 (MVP), the v1.1 stability/security
patch, and Phase 2 (M1-M6: active Cost Control Layer, quality-gate
thresholds, Creator Account Management, Publishing Agent, Metrics Ingestion
Automation, Experimentation Engine + Revenue rollups) are all now
implemented** — see `docs/PHASE1.md` for what was actually built, where,
and how it maps to the sections below; `docs/PHASE1_AUDIT.md` and
`docs/PHASE1_AUDIT_v2.md` for the production-readiness audits that gated
each phase. Phase 3 (full automation) remains design-only.
**Version:** 0.3 (final pre-implementation revision — adds campaign intelligence,
quality scoring, creator account management, cost control layer, data flywheel
architecture, expanded security/account safety, and MVP success metrics)

---

## 0. Executive Summary & Reality Check

This document designs an agentic system that discovers Whop Content Rewards
campaigns, produces short-form video at scale, publishes it across TikTok/YouTube
Shorts/Instagram Reels, and uses performance data to improve future output.

Before the architecture, three hard constraints shape every decision below and
should be read first:

1. **Whop Content Rewards has no confirmed public API for campaign discovery or
   submission tracking.** Whop exposes an API for memberships/payments, but the
   Content Rewards marketplace (browsing campaigns, submitting a video URL for
   view-tracking, seeing approval status) is, as far as I can verify, a
   dashboard-driven product. Treat automated discovery as **screen-scraping or
   semi-manual** until you've confirmed otherwise with Whop directly (check
   their developer docs / ask their support for Content Rewards API access
   before building against an assumption). The whole Research Agent design
   below is built with a human-verification fallback for exactly this reason.

2. **Platform policy has moved specifically against this business model.**
   YouTube (Shorts), TikTok, and Instagram have all tightened policy in
   2024–2025 against "inauthentic," "mass-produced," or "repetitive" content —
   which is precisely what a naive version of this system produces. This is
   not a hypothetical risk; it's the single biggest threat to the business.
   The architecture below treats **human review and de-duplication as a
   pipeline stage**, not an afterthought, and defaults to fewer, better videos
   over volume.

3. **Reported Whop Content Rewards earnings are highly variable and
   self-reported.** Payout is CPM-based (per approved 1,000 views) and set per
   campaign by the brand; budgets are often capped, meaning payout per
   creator drops as more creators join a saturated campaign. Any $ figures in
   this document are illustrative ranges for cost/ROI modeling, not promises —
   validate against live campaign terms before committing spend.

None of this means don't build it — it means build it as a **quality-gated,
low-account-count, compliance-aware system first**, and only expand
volume/autonomy once a repeatable, policy-safe pattern is proven. That
philosophy drives the phased automation scope in §16 and the roadmap in §21.

---

## 1. System Architecture

### 1.1 Layered view

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CONTROL PLANE                                │
│   Orchestrator Agent · Workflow Engine · Budget Governor · Audit Log │
└───────────┬───────────────────────────────────────────┬─────────────┘
            │                                            │
┌───────────▼───────────┐                    ┌───────────▼────────────┐
│   INTELLIGENCE LAYER   │                    │   HUMAN REVIEW GATE     │
│  Research Agent        │◄──feedback loop───►│  (compliance, brand    │
│  Script Agent          │                    │   safety, quality)     │
└───────────┬────────────┘                    └───────────┬────────────┘
            │                                              │
┌───────────▼────────────┐                    ┌────────────▼───────────┐
│   PRODUCTION LAYER      │───render jobs────► │   DISTRIBUTION LAYER    │
│  Video Production Agent │                    │  Publishing Agent       │
│  (script→video→voice→   │                    │  (TikTok/YT/IG APIs,    │
│   captions→export)      │                    │   scheduling)           │
└───────────┬─────────────┘                    └────────────┬───────────┘
            │                                                │
            │                    ┌───────────────────────────▼──────────┐
            └───────────────────►│         FEEDBACK LAYER                │
                                  │  Analytics Agent                      │
                                  │  (metrics ingest, viral score,        │
                                  │   duplicate/improve decisions)        │
                                  └───────────────────┬────────────────────┘
                                                       │
      ┌────────────────────────────────────────────────▼───────────────────┐
      │  CAMPAIGN INTELLIGENCE (§3) → CONTENT INTELLIGENCE LAYER (§4) →     │
      │  EXPERIMENTATION ENGINE (§5) → QUALITY SCORING SYSTEM (§6)          │
      │  (pre-production scoring, pattern memory, learning, pre-publish QC)│
      └────────────────────────────────────────────────┬───────────────────┘
                                                         │
                                          back to Research/Script Agents

┌─────────────────────────────────────────────────────────────────────┐
│         CREATOR ACCOUNT MANAGEMENT (§8)  ·  COST CONTROL LAYER (§10) │
│  Account health, warmup, cadence limits  ·  Spend tracking, budget    │
│                                             governor, auto cost-cuts  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│               REVENUE OPTIMIZATION MODULE (§9)                       │
│  Payout · views · RPM · production cost · profit-per-video/niche     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│               DATA FLYWHEEL (§11 — synthesis of all the above)       │
│  Every publish → labeled outcome → better scripts/hooks/niches/cost  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                            DATA LAYER                                 │
│  Postgres (relational) · pgvector/vector store (pattern memory) ·     │
│  Object storage (video/audio assets) · Redis (queue/cache)            │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Design principles

- **Event-driven pipeline, not a monolith.** Each agent consumes from and
  publishes to a queue/workflow engine. Any stage can be paused, retried, or
  replaced without touching the others.
- **State machine per content item.** Every piece of content moves through
  explicit states: `discovered → scored → scripted → produced → quality-scored
  → reviewed → scheduled → published → tracking → scored → archived`. This
  gives a single source of truth for "where is everything" and makes both the
  quality-scoring step and the human review gate first-class states, not
  bolt-ons.
- **Durable execution.** Video generation and platform publishing involve
  slow, flaky third-party calls. The workflow engine must survive crashes,
  retries, and multi-hour waits (e.g., waiting 48–72h for early metrics)
  without losing state.
- **Everything is auditable.** Every agent decision (why this campaign, why
  this hook, why this score) is logged with inputs/outputs/cost/model used —
  required both for debugging and for defending against platform
  policy/appeals disputes.
- **Learning is a separate concern from doing.** Producing content (agents in
  §2) is decoupled from remembering what worked (Content Intelligence Layer,
  §4) and deciding what to try next (Experimentation Engine, §5). This split
  is what makes the system actually improve over time instead of just
  repeating whatever the last run happened to do — see the full loop
  description in §11.
- **Safety and cost controls sit outside the creative loop.** Account health
  (§8) and cost governance (§10) are not steps a creative agent can talk its
  way past — they're hard, code-enforced gates the Orchestrator checks
  independently of any agent's output.

---

## 2. AI Agent Architecture

Shared infrastructure for all agents:

- **Agent framework:** tool-calling LLM agents (Claude Sonnet 5 for
  high-volume reasoning, Claude Opus 5 for high-stakes creative/strategy
  decisions), built on a graph/workflow framework (LangGraph or an
  equivalent state-machine library) rather than free-form chat loops — each
  agent has a bounded tool set and explicit exit conditions.
- **Shared memory:** a vector store of past hooks/scripts/transcripts tagged
  with outcome (viral score, earnings) so agents retrieve "what has actually
  worked" instead of hallucinating trends — this is the Content Intelligence
  Layer described in §4.
- **Guardrails:** every agent that touches money, publishing, or brand
  claims has a hard-coded validation step (schema checks, banned-word/claim
  filters, budget ceilings) that runs in code, not just LLM judgment.

### 2.1 Research Agent

- **Inputs:** Whop campaign listings (scraped/semi-manual feed), TikTok
  Creative Center trend data, public competitor posts, Google Trends,
  historical internal performance data, Content Intelligence Layer's
  competitor briefs, Campaign Intelligence's scoring output (§3).
- **Outputs:** ranked list of candidate campaigns with a `niche_fit_score`,
  `estimated_cpm`, `saturation_estimate`, a composite `campaign_score` (§3),
  and a short competitor-pattern brief.
- **Logic:** scores campaigns on estimated payout ceiling, budget
  remaining, rule complexity/restrictiveness, niche saturation, and brand
  safety. Flags campaigns whose rules require things the system can't do
  honestly (e.g., "must be organic follower," "no AI-generated content").
- **Guardrail:** cannot auto-approve a campaign for production — always
  hands off to the Orchestrator for a go/no-go, and campaigns whose rules
  explicitly prohibit AI-generated content, or whose Campaign Score falls
  below the defined floor (§3), are auto-rejected.

### 2.2 Script Agent

- **Inputs:** approved campaign brief, niche research, high-performing
  hook/script patterns retrieved from the Content Intelligence Layer (§4),
  current experiment assignments from the Experimentation Engine (§5).
- **Outputs:** 3–5 script variants per concept (for A/B testing), each with
  a hook (first 1–3 seconds), body beats, CTA, target duration, and
  suggested pacing for captions.
- **Logic:** explicitly optimizes for the stated algorithm factors — writes
  hooks for retention in the first 3 seconds, structures content for
  rewatch loops (e.g., list/countdown formats), and inserts natural
  share/comment triggers (controversial-but-safe opinions, "tag someone
  who…", open loops).
- **Guardrail:** brand-safety and factual-claim filter; a "no fabricated
  stats/claims about the sponsoring product" rule, since these are
  sponsored/branded videos and false claims create legal exposure for the
  brand and the creator account.

### 2.3 Video Production Agent

- **Inputs:** selected script variant, brand assets (if any), style/template
  config for the niche/account.
- **Pipeline:** script → TTS voiceover → visual assembly (stock/b-roll,
  templated motion graphics, or AI-generated clips depending on
  niche/budget) → auto-captioning with word-level timing → effects/pacing →
  render → thumbnail extraction → **Quality Scoring System (§6)**.
- **Outputs:** rendered video file(s) per variant, metadata (duration,
  voice used, caption style, whether any segment is fully AI-generated —
  needed for platform AI-disclosure labeling), an automated QC report, and
  the four quality scores from §6.
- **Design choice:** favor a **templated programmatic editing pipeline**
  (real/stock footage + motion templates + TTS + captions) over fully
  generative AI video for the MVP. It's cheaper, faster, more consistent in
  quality, and far less likely to read as "AI slop" to both viewers and
  platform authenticity filters. Fully generative video (Runway/Kling/etc.)
  is a later-phase option for niches where it fits — see §14 and §21.
- **Guardrail:** automatic AI-disclosure tagging metadata attached to any
  video containing synthetic voice/visuals, so the Publishing Agent can
  apply the correct platform label.

### 2.4 Publishing Agent

- **Inputs:** approved, rendered video + metadata, posting-time guidance
  from the Experimentation Engine (§5), account availability/cadence
  budget from Creator Account Management (§8).
- **Outputs:** scheduled/published post per platform with optimized title,
  description, hashtags, and posting time; applies required AI-content and
  branded-content/paid-partnership disclosure per platform.
- **Logic:** posting-time optimization from historical account-level
  performance, hashtag selection from trend + niche saturation data,
  per-platform format adaptation (aspect ratio, caption burn-in style,
  max duration).
- **Guardrail:** hard rate limits per account modeled on organic human
  posting cadence (not "as fast as the API allows") and enforced against
  that account's current health tier (§8b) — the single most important
  lever for account survival. Also enforces platform API quota budgets
  (see §13) and blocks duplicate/near-duplicate content across accounts to
  avoid inauthentic-behavior detection.

### 2.5 Analytics Agent

- **Inputs:** platform analytics APIs (views, average watch time,
  completion, shares, comments, likes, saves where available), Whop
  earnings data.
- **Outputs:** `viral_score` per video using the specified weighting,
  earnings-per-video, and a `duplicate | iterate | retire` recommendation
  per content pattern — handed to the Content Intelligence Layer and
  Experimentation Engine for storage and decision-making respectively, and
  compared against the pre-publish Quality Scores (§6) to recalibrate
  those predictive models (§11).
- **Viral Score formula (as specified):**

  ```
  Viral Score = 0.40 × normalized(watch_time)
              + 0.25 × completion_rate
              + 0.15 × normalized(shares)
              + 0.10 × normalized(comments)
              + 0.10 × normalized(likes)
  ```

  Note: "watch time," "shares," and "comments" need per-platform
  normalization (e.g., z-score against that account's/niche's trailing
  30-day baseline) before weighting, or the score is meaningless across
  videos with wildly different reach. Rewatch rate and engagement velocity
  (views in first hour/day) aren't in the stated formula but are valuable
  leading indicators — recommend tracking them as secondary signals that
  gate *when* a video is scored, even though they don't get formula weight.
- **Guardrail:** flags anomalous view patterns (sudden spikes inconsistent
  with engagement) for human review — both to protect against being
  mistaken for using view bots, and to catch actual fraud from bad actors
  in a shared campaign.

### 2.6 Orchestrator Agent

- **Role:** owns the state machine described in §1.2, sequences the other
  five agents, enforces the budget governor (§10c, using data from the
  Revenue Optimization Module, §9), enforces account-health gating (§8),
  and is the only agent allowed to transition content out of the human
  review gate.
- **Not an LLM free-for-all:** implemented primarily as deterministic
  workflow code (e.g., Temporal.io workflow) that *calls* LLM agents as
  steps, rather than an LLM deciding control flow — control flow needs to be
  debuggable and replayable, which agentic loops are bad at.

---

## 3. Campaign Intelligence

Before any script is written, every discovered campaign is scored so the
system commits production budget only where the numbers plausibly work —
this runs as an extension of the Research Agent (§2.1), before content
creation starts.

### 3.1 Campaign scoring system

```
Campaign Score = 0.35 × normalized(expected_roi)
               + 0.25 × (1 − competition_level)
               + 0.20 × (1 − difficulty_score)
               + 0.20 × niche_fit_score
```
(weights are a starting point — tune once real outcome data exists via the
flywheel, §11)

### 3.2 Estimated difficulty

Composite of: rule complexity (number/strictness of required elements —
mandatory disclosures, required mentions, footage/asset requirements),
whether we already hold the needed brand assets, any knowable approval
turnaround/strictness history, and minimum account requirements (follower
count, account age) the campaign imposes. Higher difficulty lowers the
score — a campaign that's technically high-paying but operationally hard to
execute correctly isn't automatically a good bet.

### 3.3 Competition level

Inferred from observable signals — count of distinct creators visibly
posting under the campaign's tracked hashtag/sound/brand mention in a
rolling window, sourced from the Content Intelligence Layer's competitor
analysis pipeline (§4.1). More creators chasing a capped budget means a
shrinking view-share and payout per creator; this is modeled explicitly
rather than discovered the hard way after committing production budget.

### 3.4 Expected ROI before creating content

Uses the Revenue Optimization Module's historical production-cost baselines
(§9) plus the campaign's stated CPM/budget cap to project a payout range
across conservative/median/optimistic view-outcome scenarios, each weighted
by the niche's historical hit-rate (from Content Intelligence pattern
confidence, §4.3). Output is always a **range with explicit uncertainty**,
never a single guaranteed number — pre-publish performance prediction is
inherently uncertain, and presenting false precision here would undermine
the whole scoring system's credibility.

**Guardrail:** the Orchestrator will not greenlight a full production run
(beyond a small initial test batch) for a campaign whose Campaign Score
falls below a defined floor, or whose *pessimistic*-case expected ROI is
net-negative after production cost.

**New schema:**
```
campaign_scores
  id, campaign_id, computed_at, expected_roi_low, expected_roi_median,
  expected_roi_high, difficulty_score, competition_level, niche_fit_score,
  composite_score, recommendation
```

---

## 4. Content Intelligence Layer

This is the system's **persistent memory** — distinct from any single
agent's per-run context, and distinct from Campaign Intelligence's
pre-production scoring (§3). Analytics Agent produces raw measurements;
this layer is where those measurements become durable, queryable knowledge
that Research and Script agents draw on. Four components:

### 4.1 Competitor Analysis

- Maintains a watchlist of competitor/creator accounts per niche
  (`tracked_accounts` table); also the data source Campaign Intelligence's
  competition-level scoring (§3.3) reads from.
- Collects, on a recurring schedule: post cadence, hook style (transcribed
  first line), format (talking-head / UGC / voiceover+b-roll / text-overlay),
  publicly-visible engagement signals, and niche positioning.
- Produces a recurring **competitive brief per niche**: what's trending up,
  what's saturated, what formats are emerging.
- **Guardrail:** competitor content is used for *pattern inspiration and
  tagging only* — transcribed hooks are stored labeled "observed, not
  owned" and never reused verbatim, both to avoid plagiarism claims and
  because copied content is itself a duplicate-content risk (checked again
  at the Quality Scoring System's originality score, §6a).

### 4.2 Viral Hook Database

- Structured library of hooks tagged by: niche, `hook_type` (question /
  shock-stat / controversy / story-open / callout / countdown / etc.),
  performance outcome (viral score, 3-second retention), and source
  (`internal-generated` vs. `competitor-observed`).
- Populated automatically: every script's hook and its eventual performance
  is logged; competitor hooks are added via §4.1 with the observed-only tag.
- Retrieval: the Script Agent does a similarity search (via hook-text
  embeddings) filtered by niche and by performance, so new hooks are
  generated **grounded in evidence**, not LLM guessing.

### 4.3 Winning Video Pattern Storage

- Stores whole-video patterns, not just hooks: structure (hook → problem →
  solution → CTA, countdown, POV-story, etc.), pacing (cuts/second, caption
  style), length, and voice style — combined with outcome metrics.
- Extends the `learning_patterns` table (§12) with a `pattern_template_id`
  the Video Production Agent can directly reuse.
- **Confidence tiers, not binary win/lose:** a pattern is `candidate` until
  it has a defined minimum number of supporting videos (e.g., ≥5) with
  consistent results, at which point it's promoted to `confirmed`. This
  guards against declaring a "winning pattern" off 1–2 lucky videos.

### 4.4 A/B Testing Framework

- Every content idea can generate N variants (script/hook, and optionally
  thumbnail, caption style, or posting time) sharing an `experiment_id` and
  an `experiment_group` label (control / variant A / B / C).
- **Sequential testing** (variants posted at different times on one
  account) is available from day one but is explicitly noted as
  confounded. **Parallel testing** (comparable variants across ≥2 similar
  accounts) is preferred once the account portfolio (§8) supports it.
- Defines minimum sample size and a significance threshold before a test is
  allowed to "conclude" — prevents small-sample false confidence.
- Results feed directly into the Experimentation Engine (§5).

---

## 5. Experimentation Engine

Where the Content Intelligence Layer stores what happened, the
Experimentation Engine decides **what to try next**, across four axes.
This is implemented as a scheduled statistical job (e.g., weekly), not
ad-hoc LLM judgment — significance testing and budget allocation need to be
deterministic and auditable.

- **Hooks:** promotes a hook pattern from `candidate` to `confirmed winner`
  for a niche once it beats the niche baseline viral score by a defined
  margin across the minimum sample size (§4.4); demotes patterns whose
  performance decays over time.
- **Niches:** tracks trailing-window ROI per niche (from the Revenue
  Optimization Module, §9, and Campaign Intelligence's realized-vs-predicted
  accuracy, §3) and reallocates production slots using a bounded
  exploration/exploitation split — e.g., an epsilon-greedy approach where
  the large majority of new production slots go to proven top niches, and a
  small fixed percentage is reserved for exploring unproven ones.
- **Video length:** buckets published videos by duration per platform
  (platforms reward different length bands differently) and tracks viral
  score/completion rate by bucket, feeding the winning bucket back into the
  Script Agent's `target_duration_s` guidance.
- **Posting times:** tracks viral score/engagement velocity by
  (platform, account, day-of-week, hour-bucket), converging toward each
  account's own best windows over time (§8), not just a global default.
  **Caveat:** this axis needs the most data density and is the slowest to
  reach statistical reliability at low MVP volume.

**Guardrail:** all exploration spend is bounded by the Cost Control Layer's
budget governor (§10c) — the engine can propose reallocating toward a
promising signal, but can't unilaterally exceed the budget ceiling.

**Honest caveat for Phase 1:** at 15–20 videos/month, most of these axes
will not reach statistical significance yet. That's expected — Phase 1's
real job is generating the labeled data this engine (and the Data Flywheel,
§11) will use once Phase 2/3 volume makes the tests meaningful.

---

## 6. Quality Scoring System

Before a video reaches the human review gate, it receives four automated
scores — giving both the reviewer and the Orchestrator objective inputs
(not just vibes) for the approve/reject decision, and creating another
labeled signal for the flywheel (§11).

### 6a. Originality score

Measures similarity to prior published content (internal library) and to
tracked competitor content (§4.1), via embedding similarity on
script/hook text and, where feasible, audio/visual fingerprinting of the
rendered video. A low score (near-duplicate) auto-flags — duplicate/
near-duplicate content is both a platform-policy risk and a Whop
fraud-detection risk (§17). Below a configurable floor, the video is
auto-rejected before it consumes a human review slot.

### 6b. Retention prediction score

Predicts expected completion rate/watch time before publish — starting as
a heuristic/regression over script features (hook type, pacing, length,
the pattern's historical performance from §4.3), graduating to a learned
model once enough labeled outcome data exists. Used to prioritize the
review queue (fast-track likely winners, add scrutiny to likely
underperformers) and, critically, its prediction-vs-actual gap is what
recalibrates the model over time (§11).

### 6c. Policy risk score

Automated check against known platform policy risk factors: AI-disclosure
completeness, banned-claim/word filter hits, copyright-risk footage/audio/
music flags, brand-safety topic classification, and campaign-rule
compliance (parsed from `rules_json`). A high score is a **hard block from
auto-publish in every phase, including Phase 3** — this is the one quality
score with no full-automation bypass.

### 6d. Monetization probability score

Estimates the likelihood this video produces Whop-*approved* (not just
raw) views meeting the campaign's payout criteria — combining retention
prediction, policy risk (rule violations get views rejected even if the
video is popular), and the niche/campaign's historical approval-rate data.
This is the score most directly answering "will this actually make money,"
complementing (not duplicating) the Revenue Optimization Module (§9),
which measures realized outcomes after the fact rather than predicting
them beforehand.

All four scores are attached to the video record and shown on the
reviewer dashboard as **inputs, not verdicts** — the human retains final
say (§7). Floor thresholds per score (below/above which auto-reject
happens without consuming a review slot) are configurable per phase —
stricter by default early on, loosened only by explicit human decision as
confidence in the models grows.

**New schema:**
```
quality_scores
  id, video_id, originality_score, retention_prediction_score,
  policy_risk_score, monetization_probability_score, computed_at,
  model_version
```

---

## 7. Human-in-the-Loop Growth Strategy

### 7.1 How humans review videos (the mechanics)

- Review happens on the internal dashboard, at the `produced → reviewed`
  state transition, **after** the Quality Scoring System (§6) has attached
  its four scores.
- The reviewer sees: the rendered video, script + hook, the four quality
  scores, an **auto-generated campaign-rules checklist** (parsed from the
  campaign's `rules_json`), the automated QC report, and the AI-disclosure
  metadata already attached.
- Reviewer actions are structured: **Approve** / **Reject (with a reason
  code)** / **Request revision** (e.g., "hook too aggressive," "unverified
  claim," "off-brand tone") — revisions route back to the Script or
  Production Agent *with the reason attached as context*, not as a
  black-box bounce.
- Reason codes are aggregated: a pattern or account rejected repeatedly for
  the same reason becomes a **"known-bad pattern"** entry in the Content
  Intelligence Layer.
- **Batch review** is supported: near-identical variants of one approved
  concept can be reviewed together rather than frame-by-frame.

### 7.2 What's automated in every phase, from day one

- Research + Campaign Intelligence scoring/ranking (human still makes the
  campaign go/no-go call in Phases 1–2).
- Script variant generation.
- Video rendering pipeline (TTS, captions, editing).
- Quality Scoring System (§6) — computing the scores is automatic;
  *acting* on borderline scores may or may not require a human depending
  on phase and score type (policy risk never bypasses review, §6c).
- Metrics polling and viral-score computation.
- Content Intelligence Layer updates.
- Experimentation Engine's statistical analysis — recommendations, not
  unilateral actions, until Phase 3.
- Cost Control Layer's automatic cost-*reduction* actions (§10d) — safe to
  automate since they only ever reduce spend, never raise a ceiling.
- Budget governor monitoring — auto-pausing an unprofitable niche/campaign
  is a fail-safe action.

### 7.3 What requires human approval (narrows phase over phase)

| Decision | Phase 1 (manual + AI-assisted) | Phase 2 (partial automation) | Phase 3 (full automation) |
|---|---|---|---|
| New campaign selection | Human approves every campaign | Human approves every campaign | Human approves new categories only |
| Script approval | Human reviews every script | Human reviews new patterns / flagged scripts only | Confirmed-pattern scripts auto-approved |
| Video approval | Human reviews 100% before publish | Human reviews first N of any new pattern/niche/account + anything score-flagged | Confirmed patterns auto-publish; fixed-% sampled audit (never 0%) |
| Posting schedule | Human sets/approves every slot | Auto-scheduled within pre-approved time windows | Fully automated within governor limits |
| Budget ceiling changes | Human decides | Human decides | Human decides |
| Cost-reduction actions | Automated (§10d) | Automated | Automated |
| New niche / new account | Human decides | Human decides | Human decides |
| Account health tier drop | Automated detection, human-reviewed response | Automated detection, human-reviewed response | Automated detection, human-reviewed response |
| Platform strike/restriction response | Human handles | Human handles | Human handles (never automated) |

This table is the operational core of §16's phased automation scope, which
restates it alongside the other functions that also change scope across
phases.

### 7.4 Escalation path

Any platform-issued warning, strike, or account restriction **immediately
halts automated publishing for that account and pages a human** — a hard
rule in every phase, because it's the failure mode with the highest cost
(§20).

---

## 8. Creator Account Management

The account portfolio is a managed asset with its own lifecycle, not just
a list of posting targets.

### 8a. Multiple social accounts

`owned_accounts` (§12) holds one row per platform account, each with an
explicit niche/style assignment — enforced to prevent content-mixing on a
single account, which is itself a soft inauthenticity signal. A
portfolio-level dashboard view shows health score, posting cadence vs.
cap, and warmup status per account, across the whole portfolio at a
glance.

### 8b. Account health score

A composite score per account tracking: recent posting cadence vs. cap,
engagement-rate trend against the account's *own* baseline (not a global
one — accounts differ), platform warnings/strikes count, API error rate on
that account's calls (an early signal of restriction), content-similarity
to the rest of the owned portfolio, and disclosure-compliance rate.

Tiers: **Healthy → Watch → At-Risk → Restricted.** A tier drop
automatically overrides the *system-level* automation phase for that
specific account — a Watch-tier account falls back to full human review
regardless of what phase the rest of the portfolio is running at.
Computed on a recurring schedule (e.g., daily).

### 8c. Warming new accounts

New accounts do not start posting sponsored/campaign content immediately.
A defined warmup protocol runs first: a period of organic-cadence,
non-sponsored posting at low, ramping frequency, mirroring how a real
creator builds an account, before campaign content is introduced at all.
Warmup is an explicit account state (`warming` → `active`) with defined
graduation criteria (minimum account age, minimum organic post count, no
policy flags) — applies equally to newly created accounts and to existing
accounts being onboarded into the system.

### 8d. Avoiding abnormal publishing behavior

- Posting caps are per-account and vary by health tier and account age,
  enforced by the Publishing Agent's guardrail (§2.4) regardless of queue
  pressure.
- **Posting-time jitter:** avoid perfectly regular, bot-like intervals —
  real humans don't post at the exact same second daily.
- **Cross-account de-duplication:** no two owned accounts post
  near-identical content in a way that reads as a coordinated network.
- Any abnormal-behavior signal (API errors spiking, engagement collapsing,
  platform warning) immediately drops the health tier and, if it crosses
  the Restricted threshold, triggers the same hard-stop-and-page-a-human
  flow described in §20.4.

**New schema:**
```
account_health_snapshots
  id, account_id, captured_at, health_score, tier, posting_cadence_used,
  cap_utilization_pct, engagement_trend, strikes_count, api_error_rate
```

---

## 9. Revenue Optimization Module

The system's financial instrumentation — "is this actually a business," as
distinct from the Analytics Agent's *content*-performance viral score, and
distinct from the Cost Control Layer's *active* spend governance (§10).

Tracked per video / campaign / niche:

- **Campaign payout:** actual $ earned from the `earnings` table,
  distinguishing pending vs. confirmed/paid.
- **Views:** **raw platform views** vs. **Whop-approved views** tracked
  separately — a large gap between them is itself a signal worth
  surfacing.
- **RPM (revenue per mille):** `payout ÷ (approved_views / 1000)`, tracked
  per campaign and as a rolling average per niche.
- **Production cost per video:** LLM + TTS + render cost + an assigned
  $/hour rate for human review time, pulled from the Cost Control Layer's
  ledger (§10a).
- **Profit per video:** `payout_realized − production_cost`.
- **Rollups:** profit per niche, per account, per campaign, and a trailing
  7/30-day trend.

These rollups directly drive the Experimentation Engine's niche-
reallocation weighting (§5), Campaign Intelligence's ROI-prediction
calibration (§3.4), and the Cost Control Layer's budget-governor decisions
(§10c).

**Schema:** extends `earnings` (§12) with:
```
revenue_snapshots
  id, video_id, campaign_id, captured_at,
  raw_views, approved_views, payout_realized, payout_pending,
  production_cost_usd, profit_usd, rpm_usd, status
```

**Dashboard priority:** profit-per-video and profit-per-niche should be the
single most-visible screen in the internal dashboard.

---

## 10. Cost Control Layer

Distinct from §9's after-the-fact tracking: this is the **active governor**
that enforces limits and takes automatic cost-reduction action in real
time, before and during production.

### 10a. AI generation cost tracking

Every LLM call, TTS call, and render job is logged with actual $ cost (from
provider usage APIs where available, or computed from token/second counts
× known rates) into a normalized ledger in near-real time — not
batch-reconciled after the fact, so the limits below can act on current
spend. Cost is attributed per video, per script variant, per campaign, per
niche, and per day.

### 10b. API usage limits

Per-provider quota/rate tracking (LLM tokens/min, TTS characters/day,
render-job concurrency, platform API quotas from §13), with soft warning
thresholds (e.g., 80% of daily quota) and hard stops at 100%. Tracked as
two distinct kinds of ceiling — **spend-based** and **count/quota-based**
(e.g., YouTube's daily unit budget) — since hitting one doesn't imply
hitting the other.

### 10c. Monthly budget governor

A hard, human-set monthly ceiling (per niche and system-wide) checked by
the Orchestrator before authorizing any new production run. Alerts escalate
at defined thresholds (50%, 80%, 95% of monthly budget consumed); at 100%,
new production auto-pauses (system-wide or per-niche) until a human raises
the ceiling or the next billing period starts. This is a **fail-closed**
control, consistent with §20's general principle.

### 10d. Automatic cost reduction strategies

When spend-per-video trends above a niche's historical profitable range
(from §9's data), the system can automatically:
- Fall back from generative video to templated assembly for that niche.
- Reduce script variant count per idea in niches where the Experimentation
  Engine already has high pattern confidence.
- Increase reuse of `confirmed` patterns over generating novel concepts
  when budget is tight.
- Shift render jobs to self-hosted infrastructure over managed APIs when
  managed-API costs spike.

These are automatic **cost-reduction** actions only — reducing spend needs
no human approval; raising the budget ceiling always does.

**New schema:**
```
cost_ledger
  id, agent_run_id, category ('llm'|'tts'|'render'|'platform_api'),
  provider, cost_usd, quota_units_used, recorded_at
```

---

## 11. Data Flywheel Architecture

This section explicitly answers: **how does every published video make the
system better?** It ties together Campaign Intelligence (§3), Content
Intelligence (§4), the Experimentation Engine (§5), Quality Scoring (§6),
Account Management (§8), and Revenue Optimization (§9) into one loop. It
introduces no new components — it's the connective description of how
their data interacts over time.

**The atomic unit of learning:** every publish generates a labeled 4-tuple —
`(script/hook/pattern/niche/account/posting-time chosen) → (predicted
quality scores) → (actual performance metrics) → (actual revenue/cost
outcome)`. Everything below is built from accumulating and comparing these
tuples.

1. **→ Future scripts.** The Script Agent's retrieval from the Content
   Intelligence Layer (§4) is only as good as the labeled pattern library it
   draws from. Every published video's actual viral score and revenue
   outcome updates the confidence tier of the pattern it used (§4.3),
   directly changing what the Script Agent is likely to retrieve next time.
   Rejected/low-scoring videos feed the "known-bad pattern" list (§7.1) so
   the same mistakes aren't repeated.

2. **→ Future hooks.** The Viral Hook Database (§4.2) updates each hook's
   performance stats after every publish. Comparing the **predicted**
   retention score (§6b) against the **actual** retention becomes training
   signal that recalibrates the retention predictor itself — the model
   improves precisely because real outcomes keep correcting it.

3. **→ Future niches.** The Revenue Optimization Module's per-niche profit
   rollup (§9) and Campaign Intelligence's realized-vs-predicted ROI (§3.4)
   together update the Experimentation Engine's niche-allocation weights
   (§5) — niches that pay off get more production slots; niches that don't
   get deprioritized. This is also where Campaign Intelligence's *predicted*
   difficulty/competition scores get checked against what actually
   happened, improving those predictions for the next campaign evaluated in
   that niche.

4. **→ Future production decisions.** All four Quality Scores (§6) are
   compared against actual post-publish outcomes to recalibrate their
   underlying models. The Cost Control Layer's automatic reduction
   strategies (§10d) are themselves tuned by whether a given cost-saving
   move hurt profit-per-video or not — e.g., if falling back to templated
   assembly in a niche didn't hurt viral score but did cut cost, that
   becomes the new default for that niche, not a one-off adjustment.

5. **→ Account-level learning.** Account health data (§8b) accumulates
   per-account patterns too — an account's own historical best posting
   times/cadence become what the Experimentation Engine's posting-time axis
   (§5) converges on *for that specific account*, not just a global
   default.

**Cadence:** these are several loops running at different speeds — quality-
score recalibration can happen continuously (every publish); niche/campaign
reallocation runs on the Experimentation Engine's scheduled cycle (weekly);
Campaign Intelligence's own scoring accuracy is reviewed less frequently
(e.g., monthly), since campaign-level outcomes take longer to settle (Whop
payout confirmation lag).

**Why this is the actual moat:** anyone can call an LLM to write a script or
an API to post a video. The compounding asset is the growing, labeled
record of *what specifically worked, for which niche, hook, length, and
timing, at what cost, and what it actually paid* — that's what makes month
6's content cheaper and better to produce than month 1's, without needing a
smarter underlying model.

---

## 12. Database Design (relational core, Postgres)

```
campaigns
  id, whop_campaign_id, brand_name, niche_id, payout_model, cpm_rate,
  budget_cap, budget_remaining_est, rules_json, ai_content_allowed (bool),
  discovered_at, expires_at, status, roi_score

campaign_scores (§3)
  id, campaign_id, computed_at, expected_roi_low, expected_roi_median,
  expected_roi_high, difficulty_score, competition_level, niche_fit_score,
  composite_score, recommendation

niches
  id, name, category, saturation_score, avg_cpm_est, trend_score, updated_at

tracked_accounts (competitors, for research — feeds §4.1)
  id, platform, handle, niche_id, follower_count, avg_views_est, last_scraped_at

content_ideas
  id, campaign_id, concept_summary, predicted_score, source ('research_agent'),
  status ('proposed'|'approved'|'rejected'), created_at

scripts
  id, idea_id, variant_label, hook_text, full_text, cta_text, target_duration_s,
  generated_by_model, experiment_id, experiment_group, created_at

videos
  id, script_id, render_status, asset_url, thumbnail_url, duration_s,
  voice_id, caption_style, contains_ai_voice (bool), contains_ai_visual (bool),
  qc_status, created_at

quality_scores (§6)
  id, video_id, originality_score, retention_prediction_score,
  policy_risk_score, monetization_probability_score, computed_at,
  model_version

owned_accounts (§8)
  id, platform, handle, oauth_token_ref, niche_focus_id, health_score,
  warmup_status ('warming'|'active'), daily_post_cap, status, created_at

account_health_snapshots (§8b)
  id, account_id, captured_at, health_score, tier, posting_cadence_used,
  cap_utilization_pct, engagement_trend, strikes_count, api_error_rate

publications
  id, video_id, campaign_id, account_id, platform, external_post_id,
  title, description, hashtags, scheduled_at, published_at, status

metrics_snapshots
  id, publication_id, captured_at, views, avg_watch_time_s, completion_rate,
  rewatch_rate, shares, comments, likes, saves, source ('api'|'manual')

viral_scores
  id, publication_id, computed_at, score, breakdown_json, recommendation

earnings
  id, campaign_id, publication_id, period_start, period_end,
  approved_views, payout_amount, currency, paid_at, status

revenue_snapshots (§9)
  id, video_id, campaign_id, captured_at, raw_views, approved_views,
  payout_realized, payout_pending, production_cost_usd, profit_usd,
  rpm_usd, status

cost_ledger (§10a)
  id, agent_run_id, category ('llm'|'tts'|'render'|'platform_api'),
  provider, cost_usd, quota_units_used, recorded_at

hook_library (§4.2)
  id, niche_id, hook_text, hook_type, source ('internal'|'competitor_observed'),
  best_viral_score, retention_at_3s, times_used, created_at

learning_patterns (§4.3)
  id, pattern_type ('hook'|'structure'|'niche'|'timing'), description,
  pattern_template_id, confidence_tier ('candidate'|'confirmed'|'retired'),
  supporting_publication_ids (array), confidence_score, created_at

review_decisions (§7.1)
  id, video_id, reviewer_id, decision ('approved'|'rejected'|'revision_requested'),
  reason_code, notes, decided_at

experiments (§4.4 / §5)
  id, axis ('hook'|'niche'|'length'|'posting_time'), niche_id,
  control_pattern_id, variant_pattern_ids (array), min_sample_size,
  status ('running'|'concluded'|'inconclusive'), winner_pattern_id, concluded_at

agent_runs (audit log)
  id, agent_name, campaign_id, input_ref, output_ref, model_used,
  cost_usd, latency_ms, status, created_at
```

Supplementary stores:

- **Vector store** (pgvector to start, dedicated store like Pinecone/
  Weaviate only if scale demands it): embeddings of hooks/scripts/
  transcripts tagged with outcome (backs §4).
- **Object storage** (S3 or Cloudflare R2): raw footage, rendered video/audio,
  thumbnails.
- **Redis:** job queue backing, rate-limit counters per account/platform,
  short-TTL caches.

---

## 13. API Integrations Needed

See `docs/PROVIDER_VERIFICATION_PLAN.md` (Production Hardening Sprint H7)
for the concrete sandbox/live verification checklist for TikTok, YouTube,
and Instagram — the constraints below are why none of the three has been
exercised against a live API yet.

| Integration | Purpose | Known constraints |
|---|---|---|
| **Whop** | Campaign discovery, submission, earnings | No confirmed public Content Rewards API — verify directly with Whop; design assumes dashboard scraping/semi-manual until confirmed |
| **TikTok Content Posting API** | Scheduling/publishing | Unaudited apps are limited to private/draft posting only; public posting requires app audit — budget weeks for this, not days |
| **TikTok Creative Center** | Trend data | Mostly public web data, not a formal API — scraping-based |
| **YouTube Data API v3** | Upload, metadata | Default quota ~10,000 units/day; a single upload costs ~1,600 units → roughly 6 uploads/day/project without a quota increase request |
| **YouTube Analytics API** | Performance metrics | Separate from Data API, needs its own OAuth scope |
| **Instagram Graph API (Content Publishing)** | Reels publishing | Requires Instagram Professional account linked to a Facebook Page, Meta App Review for public use, ~25–100 posts/24h/account limits |
| **Anthropic Claude API** | Research/script reasoning agents | Primary LLM |
| **ElevenLabs (or equivalent TTS)** | Voiceover generation | Per-character/minute pricing |
| **Whisper (OpenAI or self-hosted)** | Word-level caption timing | Self-hosting viable at scale to cut cost |
| **Video assembly: Remotion (self-hosted) or Shotstack/Creatomate (managed)** | Programmatic editing, captions, effects | Managed = faster MVP, higher marginal cost; self-hosted = cheaper at scale, more infra |
| **Generative video (Runway/Kling/Luma) — phase 2+** | Fully AI-generated visuals for niches that suit it | Expensive per-second, quality/consistency variance, higher policy risk if undisclosed |
| **Google Trends / Exploding Topics / social listening** | Trend & niche research | Mix of official and scraped sources |
| **Secrets manager (Vault / AWS Secrets Manager / 1Password)** | Credential storage for all of the above | See §17 |
| **Slack/Email** | Human review gate + alerting notifications | Standard webhook integration |

---

## 14. Recommended Technology Stack

| Layer | Choice | Why |
|---|---|---|
| Agent/reasoning services | Python + FastAPI | Best ecosystem for LLM tooling, video/audio libs |
| Agent framework | LangGraph (or equivalent) on top of Claude | Explicit state graphs beat free-form agent loops for this kind of pipeline |
| Workflow/durable execution | Temporal.io | Long-running, retry-heavy, multi-day workflows (waiting on metrics) need durable execution, not cron+queue hacks |
| MVP-simpler alternative to Temporal | Celery + Redis, or n8n for the first prototype | Faster to stand up if Temporal is too heavy for week 1 |
| LLMs | Claude Sonnet 5 (bulk reasoning/scripts), Claude Opus 5 (strategy/campaign evaluation) | Cost/quality split |
| Voice | ElevenLabs | Quality + speed for short-form |
| Captions | Whisper (word-level timestamps) | De facto standard, self-hostable |
| Video assembly (MVP) | Remotion (self-hosted, React-based programmatic video) | Cheaper at scale, full control over caption/effect templates |
| Video assembly (fast-start alt.) | Shotstack or Creatomate | Managed, faster to integrate, higher per-video cost |
| Generative video (phase 2+) | Runway / Kling / Luma, evaluated per-niche | Only where it improves conversion enough to justify cost/risk |
| Database | Postgres (Supabase or RDS) + pgvector | One system for relational + vector at MVP scale |
| Object storage | Cloudflare R2 (cheaper egress) or S3 | Video/audio assets |
| Queue/cache | Redis (+ BullMQ for job queues at MVP) | Simple, proven |
| Secrets management | HashiCorp Vault (self-hosted/scale) or cloud-native (AWS Secrets Manager) at MVP | See §17 |
| Internal dashboard | Next.js + Tailwind | Review gate UI, campaign pipeline visibility, analytics, profit view |
| Hosting (MVP) | Fly.io or Railway | Fast iteration, low ops overhead |
| Hosting (scale) | AWS (ECS/EKS) | When render/queue volume justifies the ops cost |
| Observability | Sentry (errors), Grafana+Prometheus (metrics), PostHog (product analytics) | Standard, well-supported |
| CI/CD | GitHub Actions | Matches repo hosting |

---

## 15. Automation Workflow (end-to-end)

1. **Discover & score:** Research Agent surfaces candidate campaigns
   (semi-manual feed initially) → Campaign Intelligence (§3) computes the
   composite score and ROI range → Orchestrator applies go/no-go, rejecting
   anything below the score floor or whose rules forbid AI content.
2. **Ideate & script:** Script Agent retrieves winning patterns from the
   Content Intelligence Layer (§4), generates 3–5 variants tagged with an
   `experiment_id` where applicable → automated brand-safety/claim filter →
   human review per §7.3's phase table.
3. **Produce & quality-score:** Video Production Agent renders each
   variant → automated technical QC → **Quality Scoring System (§6)**
   attaches originality, retention-prediction, policy-risk, and
   monetization-probability scores → **human review gate** (§7.1),
   informed by those scores, with scope per §7.3's phase table.
4. **Publish:** Publishing Agent checks the target account's health tier
   (§8b) and cadence budget, schedules approved videos across the correct
   platform(s), applies AI-disclosure and branded-content labels, avoids
   near-duplicate cross-posting.
5. **Track:** Analytics Agent polls metrics on a schedule (1h, 6h, 24h,
   72h, 7d checkpoints) and Whop earnings data as it settles; Cost Control
   Layer (§10) logs realized cost in parallel.
6. **Score & learn:** Viral score computed at checkpoints → `duplicate /
   iterate / retire` decision → outcome written back to the Content
   Intelligence Layer → Experimentation Engine updates confidence tiers and
   allocation weights on its next scheduled run → Quality Score models
   recalibrated against actual outcomes (§11) → next Script Agent run
   retrieves updated patterns.
7. **Governor check:** Orchestrator continuously compares realized
   payout/earnings (§9) vs. production+API cost (§10) per niche/campaign;
   auto-pauses an unprofitable niche/campaign, and independently checks
   every owned account's health tier before authorizing its next post.

---

## 16. Phased Automation Scope

Restates §7.3 alongside the other functions that also change scope across
phases, as a single reference.

| Function | **Phase 1 — Manual + AI-assisted** | **Phase 2 — Partial automation** | **Phase 3 — Full automation** |
|---|---|---|---|
| Campaign discovery & scoring | Human browses Whop dashboard; Campaign Intelligence scores what's manually collected | Semi-automated feed (if scraping confirmed viable); scoring fully automated | Same, higher refresh frequency |
| Campaign approval | Human approves every campaign | Human approves every campaign | Human approves new categories only |
| Script generation | Script Agent generates; human reviews all | Human reviews new patterns only | Fully automated for confirmed patterns |
| Video production | Templated assembly only | Templated; generative video piloted in 1 niche | Templated + generative where proven |
| Quality scoring | Computed but treated as informational only (models un-calibrated) | Used to prioritize/auto-reject at conservative thresholds | Used at tuned thresholds; policy-risk still always human-gated |
| Video review | 100% human review | First N of any new pattern + all score-flagged | Confirmed patterns auto-publish; fixed-% sampled audit |
| Publishing | Manual or semi-automated | API-automated, human-approved schedule windows | Fully automated within governor limits |
| Account management | 1–2 accounts, manual health checks | 3–5 accounts, automated health scoring + warmup | Broader portfolio, automated health gating |
| Cost control | Manual cost tracking | Automated ledger + governor alerts | Automated ledger + auto cost-reduction actions live |
| Revenue optimization | Manual, spreadsheet-level | Automated profit-per-video dashboard | Drives niche reallocation automatically |
| Experimentation engine | Data collection only (too little volume for significance) | Recommendations reviewed by human before acting | Acts automatically within governor + budget bounds |
| Platform strikes/restrictions | Human handles | Human handles | Human handles — never automated, any phase |

Roughly maps to the 90-day roadmap (§21) as Phase 1 ≈ days 1–30, Phase 2 ≈
days 31–60, Phase 3 ≈ days 61–90 — but the phase gates are **evidence-based,
not calendar-based**: don't move to the next phase's automation scope until
the current phase's success criteria (§23) are actually met.

---

## 17. Security and Account Safety

### 17.1 Secrets management

All credentials (LLM/API keys, OAuth tokens, database credentials) live in
a dedicated secrets manager (HashiCorp Vault self-hosted, or a cloud-native
option like AWS Secrets Manager at MVP scale) — never in `.env` files
committed to version control, never in plaintext config, never passed as
CLI arguments (visible in process lists). Secrets are injected into runtime
environments at deploy/start time, scoped per-service (a render worker
doesn't need Whop credentials; a publishing service doesn't need TTS keys).

### 17.2 API key protection

- Least-privilege scoping: every API key/token requests only the scopes it
  needs (e.g., a YouTube upload key doesn't also get Analytics read access
  unless that specific service needs both).
- Separate keys per environment (dev/staging/prod) — a leaked dev key
  should never be able to touch production accounts or spend.
- Automated secret-scanning in CI (e.g., gitleaks/truffleHog equivalents)
  blocking any commit containing a credential pattern.
- Scheduled rotation, with immediate rotation on any suspected exposure.
- Keys never appear in application logs, error messages, or the
  `agent_runs`/`cost_ledger` audit tables — those store references/IDs, not
  raw secrets.

### 17.3 Social account security

- Two-factor authentication enforced on every owned social account, tied
  to organization-controlled recovery email/phone — never an individual's
  personal contact info, which would create a single point of failure and
  a handoff risk.
- OAuth-based API access preferred over stored username/password
  credentials wherever the platform supports it.
- Session/device management reviewed periodically — flag logins from
  unexpected locations/devices as an account-health signal (§8b).
- Account recovery process documented and tested *before* it's needed —
  losing access to a monetizing account with no recovery path is a
  entirely avoidable failure mode.
- No credential sharing outside the system's secrets manager — not even
  between team members via chat/email.

### 17.4 Audit logs

- Every agent action (`agent_runs`), every cost event (`cost_ledger`),
  every human review decision (`review_decisions`), and every publishing
  action is logged with actor, inputs/outputs referenced, timestamp, and
  outcome.
- Logs are **append-only** — no deletion or in-place editing, since these
  records may be needed to defend against platform policy disputes or
  Whop fraud investigations after the fact.
- Access to audit logs is itself access-controlled (RBAC, §17.5) —
  logging who reviewed a video is only useful if that log can't be quietly
  edited by the reviewer later.
- Defined retention period (e.g., 12+ months) balancing storage cost
  against the realistic window in which a dispute or appeal could arise.

### 17.5 Additional considerations (carried from v0.2)

- **Access control:** RBAC on the internal dashboard — who can approve
  publishing, who can see financials/tokens.
- **No view manipulation, ever.** No view-bots, click farms, or engagement
  pods — a Whop ban risk, a platform ban risk, and it poisons the data
  flywheel (§11) with fake signal.
- **Content moderation:** automated filter for brand-unsafe topics,
  copyright-risk footage/music, and factual claims (feeds the Quality
  Scoring System's policy-risk score, §6c).
- **AI disclosure compliance:** platform AI-content labeling and
  FTC-style endorsement-disclosure rules are non-negotiable.
- **Data privacy:** basic GDPR/CCPA handling (export/delete) if the system
  ever accepts brand or third-party creator data beyond owned accounts.

---

## 18. Cost Estimation (illustrative — validate against current vendor pricing)

| Scale tier | Videos/day | Approx. monthly cost | Primary drivers |
|---|---|---|---|
| MVP (1 niche, 1–2 accounts) | 3–5 | **$400–$900/mo** | LLM calls (low), TTS (low), managed video assembly if used, hosting |
| Growth (3–5 niches) | 15–25 | **$1,500–$3,500/mo** | More TTS/render volume, possible self-hosted Remotion to cut per-video cost, more human review hours |
| Scale (10+ niches, many accounts) | 75–150+ | **$6,000–$15,000+/mo** | Self-hosted rendering infra, higher LLM volume, dedicated infra ops, generative-video experiments if adopted |

Rough **per-video cost** at MVP using templated assembly (not generative
video): $3–$10. Generative video (Runway/Kling-class) can push this to
$20–$60+/video — a major reason to treat it as a phase-2+ experiment. **Do
not build a cost model assuming a specific Whop CPM** — get current live
campaign terms first. The Cost Control Layer (§10) and Revenue Optimization
Module (§9) replace these illustrative estimates with real, tracked numbers
once Phase 1 is running.

---

## 19. Scalability Plan

- **Horizontal render workers:** rendering is the most parallelizable,
  resource-heavy stage — scale worker pool independently of the
  reasoning/agent services.
- **Template reuse over novel generation:** cache and reuse proven visual/
  caption templates per niche (§4.3); only pay for full LLM+render cost on
  new concepts.
- **Progressive autonomy, not progressive volume-first:** the safest
  scaling axis is narrowing the human-approval scope per §16's phase table
  on proven, low-risk niches/patterns — not simply increasing daily video
  count.
- **Account portfolio strategy:** grow by adding independently-operated
  accounts with distinct content styles/niches (§8), each going through
  its own warmup (§8c), rather than cloning one account's content across
  many handles.
- **Multi-tenant path:** the schema in §12 is already tenant-shaped
  (`owned_accounts`, `campaigns`, etc. all foreign-key cleanly to an
  `organization_id` you'd add) — treat as a phase 3+ decision.

---

## 20. Failure Scenarios & Resilience Playbook

General principle across every scenario below: **fail closed for anything
publish- or spend-related** (pause and wait for a human), **fail open for
anything read/analysis-related** (a missed data point is cheap; an
unauthorized publish or continued spend during a problem is not).

### 20.1 No campaign available (niche is dry)

- **Detection:** Research Agent's scan returns zero campaigns meeting the
  Campaign Intelligence score floor (§3) for N consecutive cycles.
- **Automated response:** Orchestrator pauses production allocation for
  that niche; the Experimentation Engine (§5) reallocates exploration
  budget to the next-ranked niche.
- **Human role:** a recurring digest flags "niche X dry for N cycles";
  human decides retire vs. wait out a seasonal cycle.

### 20.2 Video rejected (by the brand/campaign, post-submission)

Distinct from an internal review rejection (§7.1) — this is Whop/brand
disapproving a submitted video.

- **Detection:** Whop dashboard/status shows rejected/disapproved (manual
  check in Phase 1, polled where possible later).
- **Automated response:** log the rejection reason; tag the underlying
  script/pattern as `rejected_by_brand` (distinct from internal rejection);
  halt further submissions of that pattern to that campaign.
- **Human role:** review the reason — feed a fixable rule
  misunderstanding back into Script Agent guardrails; deprioritize an
  arbitrary or bad-fit brand in Research/Campaign Intelligence scoring.

### 20.3 Low retention / underperformance after publish

- **Detection:** Analytics Agent checkpoint shows completion rate/watch
  time below the niche baseline, or a large gap between the pre-publish
  retention prediction score (§6b) and actual outcome.
- **Automated response:** Experimentation Engine marks the specific
  pattern as underperforming (subject to the §4.3 sample-size guardrail);
  no further variants or spend against that idea; the gap itself feeds
  the flywheel's model recalibration (§11).
- **Human role:** escalated only when a *previously confirmed* pattern
  starts failing repeatedly — that's a real signal worth investigating.
  Isolated misses on new/candidate patterns are expected noise.

### 20.4 Platform account restriction / shadowban / suspension

- **Detection:** posting API returns restriction-indicating errors, a
  sudden metrics collapse inconsistent with content quality, an explicit
  platform notice, or the account health score (§8b) crossing into
  Restricted.
- **Automated response:** **immediate hard-stop of all automated
  publishing to that account** — fails closed without exception, and does
  **not** attempt to route around the restriction (e.g., a replacement
  account), since that reads as evasion and escalates policy risk.
- **Human role:** mandatory manual review before resumption — appeal if
  warranted, and a root-cause pass that also audits other accounts sharing
  infrastructure or content patterns with the restricted one.

### 20.5 API limitations (quota exhaustion, rate limits, provider outage)

- **Detection:** quota-remaining tracking hitting the Cost Control Layer's
  warning threshold (§10b), HTTP 429s, provider status incidents.
- **Automated response:** queue backpressure — the Publishing Agent defers
  rather than drops work (durable workflow engine handles retry-with-
  backoff); Orchestrator reprioritizes remaining queued items by expected
  value if only partial quota remains.
- **Human role:** escalated only if an outage persists beyond a defined
  threshold with real business impact.

### 20.6 Monthly budget ceiling reached

- **Detection:** Cost Control Layer's monthly governor (§10c) hits 100%
  of the human-set ceiling.
- **Automated response:** new production auto-pauses (system-wide or
  per-niche, depending on how the ceiling was set) — fails closed by
  design, since this is a hard financial control, not a soft warning.
- **Human role:** decide whether to raise the ceiling (always a manual
  decision, §7.3) or wait for the next billing period; review which
  niches/campaigns consumed the budget via the Revenue Optimization
  Module (§9) before deciding.

### 20.7 Account health tier drop without an explicit platform strike

- **Detection:** account health score (§8b) crosses from Healthy/Watch
  into At-Risk based on leading indicators (engagement collapse, rising
  API error rate) even though no explicit platform warning has been
  issued yet.
- **Automated response:** that account automatically falls back to
  full human review regardless of the system's current automation phase
  (§8b); posting cadence for that account is reduced, not just flagged.
- **Human role:** investigate the leading indicator before it becomes an
  actual restriction (§20.4) — this scenario exists specifically to catch
  problems *before* they escalate to the harder failure mode.

---

## 21. 90-Day Roadmap

**Phase 1 — Days 1–30: Prove one profitable, policy-safe pipeline**
- Confirm Whop Content Rewards access model directly with Whop (API vs.
  manual) — this blocks everything else, resolve it first.
- Pick 1 niche, 1–2 owned accounts (through warmup, §8c, if newly created).
- Build: Research Agent + basic Campaign Intelligence scoring (§3), Script
  Agent, templated Video Production Agent, a first-pass Quality Scoring
  System (§6, informational only at this stage), manual publishing, manual
  metrics + cost entry, basic viral-score calculation.
- Human review gate on 100% of output (§16 Phase 1 column).
- Goal: 15–20 published videos, at least one repeatable winning pattern
  identified, first real Whop payout data collected, first labeled dataset
  for the flywheel (§11) started.

**Phase 2 — Days 31–60: Automate the loop, add guardrails**
- Automate publishing via platform APIs (start TikTok/YouTube app-review
  processes early — they take weeks).
- Automate metrics ingestion via APIs where available.
- Stand up the Content Intelligence Layer for real and wire Script Agent
  retrieval to it; turn on the Experimentation Engine in recommend-only
  mode.
- Automate the Cost Control Layer's ledger and governor alerts; automate
  the Revenue Optimization Module's profit-per-video dashboard.
- Automate account health scoring (§8b) across the (now 3–5 account)
  portfolio.
- Calibrate Quality Scoring System thresholds against real outcome data.
- Goal: positive ROI demonstrated on at least 2 niches; internal dashboard
  live for pipeline, quality-score, and profit visibility.

**Phase 3 — Days 61–90: Controlled scale**
- Narrow the review-gate scope further on proven, low-risk niches/patterns
  per §16 Phase 3 (never to zero — sampled audits stay permanent).
- Let the Experimentation Engine and Cost Control Layer's automatic
  cost-reduction strategies act within governor bounds.
- Expand account portfolio with distinct styles per niche, each warmed
  individually.
- Evaluate a generative-video pilot for 1 niche where it fits.
- Decide: stay internal-only, or begin multi-tenant productization.
- Goal: repeatable, documented playbook per niche; system runs with
  reduced daily human intervention while staying within a defined
  review-sampling rate; flywheel data (§11) visibly improving cost and
  performance per video month-over-month.

---

## 22. MVP Definition

**In scope:**
- 1 niche, 1–2 owned/authorized accounts (warmed if new), manual-assisted
  campaign discovery with basic Campaign Intelligence scoring (§3).
- Research Agent producing a written competitor/trend brief.
- Script Agent producing 3 variants per concept.
- Templated video production (stock/b-roll + TTS + Whisper captions), no
  generative video.
- Quality Scoring System computing all four scores (§6), shown to
  reviewers as informational inputs even before the underlying models are
  well-calibrated.
- Mandatory human review before every publish (§7.1).
- Manual or semi-automated publishing to 1–2 platforms, with 2FA and
  org-controlled recovery on every account (§17.3).
- Manual/semi-automated metrics collection feeding basic viral-score
  calculation.
- Manual cost tracking (even spreadsheet-level) so profit-per-video is
  known from day one, not just view counts.
- Secrets stored in a real secrets manager from day one (§17.1) — this is
  cheap to do correctly from the start and expensive to retrofit.
- A minimal internal dashboard showing pipeline state, quality scores, and
  profit-per-video.

**Explicitly out of scope for MVP:** fully autonomous publishing, generative
video, multi-tenant support, large account portfolios, reduced-review
autonomy, an acting (vs. data-collecting) Experimentation Engine, automated
cost-reduction actions. These are earned by Phase 2/3 evidence per §16, not
assumed upfront.

---

## 23. MVP Success Metrics

Measurable targets for the first 30 days (Phase 1). These are deliberately
modest and calibration-focused — the point of Phase 1 is proving the loop
works end-to-end and generating real data, not maximizing volume.

| Metric | Target (first 30 days) | How measured | Why this bar |
|---|---|---|---|
| Videos produced | 15–20 published | Count of `publications` with `status = published` | Enough volume to get a first real signal without outrunning review capacity |
| Approval rate (human review) | ≥ 60% approved on first pass | `review_decisions` approved ÷ total reviewed | Meaningfully below 60% signals a Script/Production quality problem upstream, not a review-gate problem |
| Average retention (completion rate) | Meet or beat the niche's own competitor baseline (from §4.1) — no fixed universal % since this varies hugely by platform/niche | `metrics_snapshots.completion_rate` vs. `tracked_accounts` baseline | A fixed global target would be meaningless across niches/platforms; relative-to-competitor is the honest bar |
| Cost per video | $3–$10 (templated assembly) | `cost_ledger` sum per video | Matches §18's MVP cost model; a persistent overshoot should trigger review of the Cost Control Layer setup (§10) even before Phase 2 turns on auto cost-reduction |
| Profit per video | ≥ $0 (breakeven) minimum; positive on at least one repeatable pattern | `revenue_snapshots.profit_usd` | Phase 1's real goal is finding *one* profitable, repeatable pattern — not average profitability across everything tried |
| Platform policy strikes | 0 | Manual/automated detection (§20.4) | Any strike in Phase 1 is a stop-and-review event, not a metric to average away |
| Whop integration clarity | Confirmed answer on API vs. manual workflow | N/A — a yes/no deliverable, not a number | Blocks every automation decision in Phase 2, must be resolved by end of Phase 1 |

---

## Open Questions for You (need answers before Phase 1 build starts)

1. Do you already have a Whop Content Rewards partner/API relationship, or
   does discovery start from the public dashboard?
2. Which niche(s) do you want to start with — do you already have a
   campaign in mind, or should Research Agent design start from scratch?
3. Do you have existing TikTok/YouTube/Instagram accounts to use, or does
   account creation/warmup (§8c) need to be part of Phase 1?
4. Any hard budget ceiling for the first 30 days (LLM/API/tooling spend) to
   set as the Cost Control Layer's monthly governor (§10c)?

---

*This document (v0.3) is a design artifact only. No production code has
been written. Proceed to implementation only after explicit approval.*
