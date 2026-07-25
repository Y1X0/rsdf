# AI Content Factory — Architecture & Strategy Document

**System:** Automated content pipeline for Whop Content Rewards campaigns
**Status:** Design phase — no implementation yet
**Version:** 0.2 (revised per review — adds human-in-the-loop growth strategy,
content intelligence layer, experimentation engine, revenue optimization
module, phased automation scope, and failure scenarios)

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
philosophy drives the phased automation scope in §11 and the roadmap in §16.

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
                          ┌────────────────────────────▼───────────────────┐
                          │       CONTENT INTELLIGENCE LAYER (§3)           │
                          │  Competitor analysis · Viral hook database ·   │
                          │  Winning pattern storage · A/B test results    │
                          └────────────────────────────┬───────────────────┘
                                                        │
                          ┌────────────────────────────▼───────────────────┐
                          │       EXPERIMENTATION ENGINE (§4)               │
                          │  Hook / niche / length / posting-time bandits  │
                          └────────────────────────────┬───────────────────┘
                                                        │
                                          back to Research/Script Agents

┌─────────────────────────────────────────────────────────────────────┐
│                            DATA LAYER                                 │
│  Postgres (relational) · pgvector/vector store (pattern memory) ·     │
│  Object storage (video/audio assets) · Redis (queue/cache)            │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│               REVENUE OPTIMIZATION MODULE (§6)                       │
│  Payout · views · RPM · production cost · profit-per-video/niche     │
│  → feeds Experimentation Engine's niche allocation and the Budget     │
│    Governor's pause/continue decisions                                │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Design principles

- **Event-driven pipeline, not a monolith.** Each agent consumes from and
  publishes to a queue/workflow engine. Any stage can be paused, retried, or
  replaced without touching the others.
- **State machine per content item.** Every piece of content moves through
  explicit states: `discovered → evaluated → scripted → produced → reviewed →
  scheduled → published → tracking → scored → archived`. This gives you a
  single source of truth for "where is everything" and makes the human
  review gate a first-class state, not a bolt-on.
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
  §3) and deciding what to try next (Experimentation Engine, §4). This split
  is what makes the system actually improve over time instead of just
  repeating whatever the last run happened to do.

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
  Layer described in §3.
- **Guardrails:** every agent that touches money, publishing, or brand
  claims has a hard-coded validation step (schema checks, banned-word/claim
  filters, budget ceilings) that runs in code, not just LLM judgment.

### 2.1 Research Agent

- **Inputs:** Whop campaign listings (scraped/semi-manual feed), TikTok
  Creative Center trend data, public competitor posts, Google Trends,
  historical internal performance data, Content Intelligence Layer's
  competitor briefs.
- **Outputs:** ranked list of candidate campaigns with a `niche_fit_score`,
  `estimated_cpm`, `saturation_estimate` (how many creators already
  farming it), and a short competitor-pattern brief (what hooks/formats are
  winning in this niche right now).
- **Logic:** scores campaigns on estimated payout ceiling, budget
  remaining, rule complexity/restrictiveness, niche saturation, and brand
  safety. Flags campaigns whose rules require things the system can't do
  honestly (e.g., "must be organic follower," "no AI-generated content").
- **Guardrail:** cannot auto-approve a campaign for production — always
  hands off to the Orchestrator for a go/no-go, and campaigns whose rules
  explicitly prohibit AI-generated content are auto-rejected.

### 2.2 Script Agent

- **Inputs:** approved campaign brief, niche research, high-performing
  hook/script patterns retrieved from the Content Intelligence Layer (§3),
  current experiment assignments from the Experimentation Engine (§4).
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
  render → thumbnail extraction.
- **Outputs:** rendered video file(s) per variant, metadata (duration,
  voice used, caption style, whether any segment is fully AI-generated —
  needed for platform AI-disclosure labeling), and a quality-check report.
- **Design choice:** favor a **templated programmatic editing pipeline**
  (real/stock footage + motion templates + TTS + captions) over fully
  generative AI video for the MVP. It's cheaper, faster, more consistent in
  quality, and far less likely to read as "AI slop" to both viewers and
  platform authenticity filters. Fully generative video (Runway/Kling/etc.)
  is a later-phase option for niches where it fits (e.g., stylized/faceless
  channels) — see §9 and §16.
- **Guardrail:** automatic AI-disclosure tagging metadata attached to any
  video containing synthetic voice/visuals, so the Publishing Agent can
  apply the correct platform label.

### 2.4 Publishing Agent

- **Inputs:** approved, rendered video + metadata, posting-time guidance
  from the Experimentation Engine (§4).
- **Outputs:** scheduled/published post per platform with optimized title,
  description, hashtags, and posting time; applies required AI-content and
  branded-content/paid-partnership disclosure per platform.
- **Logic:** posting-time optimization from historical account-level
  performance, hashtag selection from trend + niche saturation data,
  per-platform format adaptation (aspect ratio, caption burn-in style,
  max duration).
- **Guardrail:** hard rate limits per account modeled on organic human
  posting cadence (not "as fast as the API allows") — the single most
  important lever for account survival. Also enforces platform API quota
  budgets (see §8) and blocks duplicate/near-duplicate content across
  accounts to avoid inauthentic-behavior detection.

### 2.5 Analytics Agent

- **Inputs:** platform analytics APIs (views, average watch time,
  completion, shares, comments, likes, saves where available), Whop
  earnings data.
- **Outputs:** `viral_score` per video using the specified weighting,
  earnings-per-video, and a `duplicate | iterate | retire` recommendation
  per content pattern — handed to the Content Intelligence Layer and
  Experimentation Engine for storage and decision-making respectively.
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
  gate *when* a video is scored (e.g., don't finalize score until velocity
  has stabilized), even though they don't get formula weight.
- **Guardrail:** flags anomalous view patterns (sudden spikes inconsistent
  with engagement) for human review — both to protect against being
  mistaken for using view bots, and to catch actual fraud from bad actors
  in a shared campaign.

### 2.6 Orchestrator Agent

- **Role:** owns the state machine described in §1.2, sequences the other
  five agents, enforces the budget governor (stops spend on a campaign/niche
  if realized ROI falls below threshold, using data from the Revenue
  Optimization Module, §6), and is the only agent allowed to transition
  content out of the human review gate.
- **Not an LLM free-for-all:** implemented primarily as deterministic
  workflow code (e.g., Temporal.io workflow) that *calls* LLM agents as
  steps, rather than an LLM deciding control flow — control flow needs to be
  debuggable and replayable, which agentic loops are bad at.

---

## 3. Content Intelligence Layer

This is the system's **persistent memory** — distinct from any single
agent's per-run context. Analytics Agent produces raw measurements; this
layer is where those measurements become durable, queryable knowledge that
Research and Script agents draw on. Four components:

### 3.1 Competitor Analysis

- Maintains a watchlist of competitor/creator accounts per niche
  (`tracked_accounts` table).
- Collects, on a recurring schedule: post cadence, hook style (transcribed
  first line), format (talking-head / UGC / voiceover+b-roll / text-overlay),
  publicly-visible engagement signals, and niche positioning.
- Produces a recurring **competitive brief per niche**: what's trending up,
  what's saturated, what formats are emerging.
- Feeds the Research Agent's `niche_fit_score` and the Script Agent's format
  selection.
- **Guardrail:** competitor content is used for *pattern inspiration and
  tagging only* — transcribed hooks are stored labeled "observed, not
  owned" and never reused verbatim, both to avoid plagiarism claims and
  because copied content is itself a duplicate-content risk.

### 3.2 Viral Hook Database

- Structured library of hooks tagged by: niche, `hook_type` (question /
  shock-stat / controversy / story-open / callout / countdown / etc.),
  performance outcome (viral score, 3-second retention), and source
  (`internal-generated` vs. `competitor-observed`).
- Populated automatically: every script's hook and its eventual performance
  is logged; competitor hooks are added via §3.1 with the observed-only tag.
- Retrieval: the Script Agent does a similarity search (via hook-text
  embeddings) filtered by niche and by performance, so new hooks are
  generated **grounded in evidence**, not LLM guessing.

### 3.3 Winning Video Pattern Storage

- Stores whole-video patterns, not just hooks: structure (hook → problem →
  solution → CTA, countdown, POV-story, etc.), pacing (cuts/second, caption
  style), length, and voice style — combined with outcome metrics.
- Extends the `learning_patterns` table (§7) with a `pattern_template_id`
  the Video Production Agent can directly reuse — this is the mechanism
  behind the "template reuse" scalability lever in §13.
- **Confidence tiers, not binary win/lose:** a pattern is `candidate` until
  it has a defined minimum number of supporting videos (e.g., ≥5) with
  consistent results, at which point it's promoted to `confirmed`. This
  guards against declaring a "winning pattern" off 1–2 lucky videos.

### 3.4 A/B Testing Framework

- Every content idea can generate N variants (script/hook, and optionally
  thumbnail, caption style, or posting time) sharing an `experiment_id` and
  an `experiment_group` label (control / variant A / B / C).
- **Sequential testing** (variants posted at different times on one
  account) is available from day one but is explicitly noted as
  confounded — you can't fully separate "this hook won" from "this time
  slot won." **Parallel testing** (comparable variants across ≥2
  similar accounts) is preferred once the account portfolio supports it.
- Defines minimum sample size and a significance threshold before a test is
  allowed to "conclude" — prevents small-sample false confidence, which is
  a common failure mode in this space.
- Results feed directly into the Experimentation Engine (§4), which is the
  decision layer that acts on them.

---

## 4. Experimentation Engine

Where the Content Intelligence Layer stores what happened, the
Experimentation Engine decides **what to try next**, across four axes.
This is implemented as a scheduled statistical job (e.g., weekly), not
ad-hoc LLM judgment — significance testing and budget allocation need to be
deterministic and auditable.

- **Hooks:** promotes a hook pattern from `candidate` to `confirmed winner`
  for a niche once it beats the niche baseline viral score by a defined
  margin across the minimum sample size (§3.4); demotes patterns whose
  performance decays over time.
- **Niches:** tracks trailing-window ROI per niche (from the Revenue
  Optimization Module, §6) and reallocates production slots using a
  bounded exploration/exploitation split — e.g., an epsilon-greedy
  approach where the large majority of new production slots go to
  proven top niches, and a small fixed percentage is reserved for
  exploring unproven ones, so the system never stops looking for the next
  winner but also never over-invests in speculation.
- **Video length:** buckets published videos by duration per platform
  (platforms reward different length bands differently — a length that
  wins on YouTube Shorts won't necessarily win on TikTok) and tracks viral
  score/completion rate by bucket, feeding the winning bucket back into the
  Script Agent's `target_duration_s` guidance.
- **Posting times:** tracks viral score/engagement velocity by
  (platform, account, day-of-week, hour-bucket). **Caveat:** this axis
  needs the most data density and is the slowest to reach statistical
  reliability at low MVP volume — early phases should default to
  published platform best-practice windows and only trust
  system-discovered windows once an account has enough throughput.

**Guardrail:** all exploration spend is bounded by the Orchestrator's
budget governor — the engine can propose reallocating toward a promising
signal, but can't unilaterally exceed the budget ceiling chasing it.

**Honest caveat for Phase 1:** at 15–20 videos/month, most of these axes
will not reach statistical significance yet. That's expected and fine —
Phase 1's real job is generating the labeled data this engine will use once
Phase 2/3 volume makes the tests meaningful. Resist the temptation to draw
firm conclusions from a handful of data points.

---

## 5. Human-in-the-Loop Growth Strategy

### 5.1 How humans review videos (the mechanics)

- Review happens on the internal dashboard, at the `produced → reviewed`
  state transition.
- The reviewer sees: the rendered video, script + hook, an
  **auto-generated campaign-rules checklist** (parsed from the campaign's
  `rules_json` — e.g., "must disclose sponsor," "must not claim X"), the
  automated QC report (audio sync, caption accuracy, duration match), and
  the AI-disclosure metadata already attached.
- Reviewer actions are structured, not just approve/reject:
  **Approve** / **Reject (with a reason code)** / **Request revision**
  (e.g., "hook too aggressive," "unverified claim," "off-brand tone") —
  revisions route back to the Script or Production Agent *with the reason
  attached as context*, not as a black-box bounce.
- Reason codes are aggregated: a pattern or account rejected repeatedly for
  the same reason becomes a **"known-bad pattern"** entry in the Content
  Intelligence Layer — the same storage mechanism as winning patterns,
  inverted.
- **Batch review** is supported: near-identical variants of one approved
  concept can be reviewed together rather than frame-by-frame, so review
  load doesn't scale linearly with variant count as volume grows.

### 5.2 What's automated in every phase, from day one

- Research Agent candidate scoring/ranking (human still makes the
  campaign go/no-go call in Phases 1–2).
- Script variant generation.
- Video rendering pipeline (TTS, captions, editing).
- Automated QC: technical (sync, duration, resolution), content
  (banned-word/claim filter), compliance (AI-disclosure tagging).
- Metrics polling and viral-score computation.
- Content Intelligence Layer updates (pattern/hook storage).
- Experimentation Engine's statistical analysis — produces
  *recommendations*, not unilateral actions, until Phase 3.
- Budget governor monitoring — auto-pausing an unprofitable niche/campaign
  is safe to automate early because it's a **fail-safe (stop spending)**
  action, not a fail-open one.

### 5.3 What requires human approval (narrows phase over phase)

| Decision | Phase 1 (manual + AI-assisted) | Phase 2 (partial automation) | Phase 3 (full automation) |
|---|---|---|---|
| New campaign selection | Human approves every campaign | Human approves every campaign | Human approves new categories only; routine renewals auto-continue |
| Script approval | Human reviews every script | Human reviews new patterns / flagged scripts only | Confirmed-pattern scripts auto-approved; new patterns still reviewed |
| Video approval | Human reviews 100% before publish | Human reviews first N videos of any new pattern/niche/account + anything QC-flagged | Confirmed patterns auto-publish; fixed-% sampled audit (never 0%) |
| Posting schedule | Human sets/approves every slot | Auto-scheduled within pre-approved time windows | Fully automated within governor limits |
| New niche entry | Human decides | Human decides | Human decides |
| New account/portfolio expansion | Human decides | Human decides | Human decides |
| Budget ceiling changes | Human decides | Human decides | Human decides |
| Platform strike/restriction response | Human handles | Human handles | Human handles (never automated) |

This table is the operational core of §11's phased automation scope —
that section restates it alongside the calendar-based roadmap for planning
purposes.

### 5.4 Escalation path

Any platform-issued warning, strike, or account restriction **immediately
halts automated publishing for that account and pages a human** — this is
a hard rule in every phase, with no exceptions, because it's the failure
mode with the highest cost (see §15).

---

## 6. Revenue Optimization Module

This is the system's financial instrumentation — "is this actually a
business," as distinct from the Analytics Agent's *content*-performance
viral score.

Tracked per video / campaign / niche:

- **Campaign payout:** actual $ earned from the `earnings` table,
  distinguishing pending vs. confirmed/paid (Whop's approval process can
  lag actual view accrual).
- **Views:** tracks **raw platform views** vs. **Whop-approved views**
  separately — these differ, since Whop pays only on views it validates
  against campaign rules. A large gap between the two is itself a signal
  worth surfacing (possible rule violation or suspect traffic being
  filtered out).
- **RPM (revenue per mille):** `payout ÷ (approved_views / 1000)`, tracked
  per campaign (RPM is set by the brand/budget) and as a rolling average
  per niche, to rank niche attractiveness.
- **Production cost per video:** LLM cost + TTS cost + render cost (managed
  API or amortized self-hosted compute) + an assigned $/hour rate for
  human review time — even internal review time should be costed, so it
  doesn't disappear from the profitability picture.
- **Profit per video:** `payout_realized − production_cost` (ideally with
  amortized posting/account-maintenance overhead layered in as the system
  matures).
- **Rollups:** profit per niche, per account, per campaign, and a
  trailing 7/30-day trend.

These rollups are not a standalone report — they directly drive two
existing components: the **Experimentation Engine's** niche-reallocation
weighting (§4) and the **Orchestrator's budget governor** pause/continue
decisions (§2.6).

New schema to support this (extends the `earnings` table in §7):

```
revenue_snapshots
  id, video_id, campaign_id, captured_at,
  raw_views, approved_views, payout_realized, payout_pending,
  production_cost_usd, profit_usd, rpm_usd, status
```

A derived view/materialized query, `niche_profitability`, rolls this up by
niche and time window for the dashboard and the Experimentation Engine.

**Dashboard priority:** profit-per-video and profit-per-niche should be the
single most-visible screen in the internal dashboard — the point of this
module is to make "are we actually making money, and where" answerable at a
glance, not buried under engagement metrics.

---

## 7. Database Design (relational core, Postgres)

```
campaigns
  id, whop_campaign_id, brand_name, niche_id, payout_model, cpm_rate,
  budget_cap, budget_remaining_est, rules_json, ai_content_allowed (bool),
  discovered_at, expires_at, status, roi_score

niches
  id, name, category, saturation_score, avg_cpm_est, trend_score, updated_at

tracked_accounts (competitors, for research — feeds §3.1)
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

owned_accounts
  id, platform, handle, oauth_token_ref, niche_focus_id, health_score,
  daily_post_cap, status, created_at

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

revenue_snapshots (§6)
  id, video_id, campaign_id, captured_at, raw_views, approved_views,
  payout_realized, payout_pending, production_cost_usd, profit_usd,
  rpm_usd, status

hook_library (§3.2)
  id, niche_id, hook_text, hook_type, source ('internal'|'competitor_observed'),
  best_viral_score, retention_at_3s, times_used, created_at

learning_patterns (§3.3)
  id, pattern_type ('hook'|'structure'|'niche'|'timing'), description,
  pattern_template_id, confidence_tier ('candidate'|'confirmed'|'retired'),
  supporting_publication_ids (array), confidence_score, created_at

review_decisions (§5.1)
  id, video_id, reviewer_id, decision ('approved'|'rejected'|'revision_requested'),
  reason_code, notes, decided_at

experiments (§3.4 / §4)
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
  transcripts tagged with outcome, used for retrieval-augmented generation
  in the Research/Script agents (backs the Content Intelligence Layer, §3).
- **Object storage** (S3 or Cloudflare R2): raw footage, rendered video/audio,
  thumbnails.
- **Redis:** job queue backing, rate-limit counters per account/platform,
  short-TTL caches.

---

## 8. API Integrations Needed

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
| **Slack/Email** | Human review gate notifications | Standard webhook integration |

---

## 9. Recommended Technology Stack

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
| Internal dashboard | Next.js + Tailwind | Review gate UI, campaign pipeline visibility, analytics, profit view |
| Hosting (MVP) | Fly.io or Railway | Fast iteration, low ops overhead |
| Hosting (scale) | AWS (ECS/EKS) | When render/queue volume justifies the ops cost |
| Observability | Sentry (errors), Grafana+Prometheus (metrics), PostHog (product analytics) | Standard, well-supported |
| CI/CD | GitHub Actions | Matches repo hosting |

---

## 10. Automation Workflow (end-to-end)

1. **Discover:** Research Agent surfaces candidate campaigns (semi-manual
   feed initially) → scores niche/saturation/payout → Orchestrator applies
   go/no-go, rejecting anything whose rules forbid AI content.
2. **Ideate & script:** Script Agent retrieves winning patterns from the
   Content Intelligence Layer (§3), generates 3–5 variants tagged with an
   `experiment_id` where applicable → automated brand-safety/claim filter →
   human review per §5.3's phase table.
3. **Produce:** Video Production Agent renders each variant → automated QC
   (duration, audio sync, caption accuracy) → **human review gate** per
   §5.1's mechanics and §5.3's phase-dependent scope.
4. **Publish:** Publishing Agent schedules approved videos across the
   correct platform(s)/account(s), respecting per-account human-cadence
   rate limits, applies AI-disclosure and branded-content labels, avoids
   near-duplicate cross-posting patterns.
5. **Track:** Analytics Agent polls metrics on a schedule (e.g., 1h, 6h,
   24h, 72h, 7d checkpoints) and Whop earnings data as it settles.
6. **Score & learn:** Viral score computed at checkpoints → `duplicate /
   iterate / retire` decision → outcome written back to the Content
   Intelligence Layer (§3) → Experimentation Engine (§4) updates
   confidence tiers and allocation weights on its next scheduled run →
   next Script Agent run retrieves updated patterns.
7. **Governor check:** Orchestrator continuously compares realized
   payout/earnings (Revenue Optimization Module, §6) vs. production+API
   cost per niche/campaign; auto-pauses a niche or campaign that's
   unprofitable after a defined sample size.

---

## 11. Phased Automation Scope

This section is the direct answer to "what's automated vs. what needs
approval, by phase" — it restates the table in §5.3 alongside the other
functions that also change scope across phases, as a single reference.

| Function | **Phase 1 — Manual + AI-assisted** | **Phase 2 — Partial automation** | **Phase 3 — Full automation** |
|---|---|---|---|
| Campaign discovery | Human browses Whop dashboard; Research Agent scores what's manually collected | Research Agent runs on a semi-automated feed (scraping if confirmed viable) | Same, at higher refresh frequency |
| Campaign approval | Human approves every campaign | Human approves every campaign | Human approves new categories only |
| Script generation | Script Agent generates; human reviews all | Script Agent generates; human reviews new patterns only | Fully automated for confirmed patterns |
| Video production | Templated assembly only | Templated assembly; generative video piloted in 1 niche | Templated + generative where proven, template-first by default |
| Video review | 100% human review | First N of any new pattern + all QC-flagged | Confirmed patterns auto-publish; fixed-% sampled audit |
| Publishing | Manual or semi-automated | API-automated, human-approved schedule windows | Fully automated within governor limits |
| Metrics collection | Manual/semi-automated entry | API-automated | API-automated, real-time |
| Content Intelligence Layer | Populated manually + by early agent runs | Actively used for retrieval in Script Agent | Primary driver of Script Agent output |
| Experimentation Engine | Data collection only (too little volume for significance) | Recommendations reviewed by human before acting | Acts automatically within governor + budget bounds |
| Revenue Optimization Module | Manual cost tracking, spreadsheet-level | Automated profit-per-video tracking, dashboard live | Drives niche reallocation automatically |
| Account portfolio | 1–2 accounts | 3–5 accounts across niches | Broader portfolio, still human-approved per new account |
| Budget governance | Human sets and monitors | Automated pause on unprofitable patterns; human sets ceiling | Automated within human-set ceiling |
| Platform strikes/restrictions | Human handles | Human handles | Human handles — never automated, any phase |

Roughly maps to the 90-day roadmap (§16) as Phase 1 ≈ days 1–30, Phase 2 ≈
days 31–60, Phase 3 ≈ days 61–90 — but the phase gates are **evidence-based,
not calendar-based**: don't move to the next phase's automation scope until
the current phase's success criteria (§17) are actually met.

---

## 12. Security Considerations

- **Credential handling:** OAuth tokens for every platform/account
  encrypted at rest (KMS-backed), scoped to least-privilege, rotated on a
  schedule, never logged in plaintext (including in `agent_runs` audit
  records).
- **Account safety:** posting cadence rate-limited to look human; avoid any
  shared-infrastructure fingerprint patterns across many accounts (same
  IP/device signatures posting near-identical content is a textbook
  coordinated-inauthentic-behavior signal on every platform).
- **No view manipulation, ever.** Do not use view-bots, click farms, or
  engagement pods — this is both a Whop ban risk and a platform ban risk,
  and undermines the entire "learn from real performance" feedback loop.
- **Content moderation:** automated filter for brand-unsafe topics,
  copyright-risk footage/music, and factual claims about sponsor products
  before the human review gate (reduces reviewer load, catches obvious
  issues early).
- **AI disclosure compliance:** treat platform AI-content labeling
  (TikTok/YouTube/Meta all have 2023–2025 rules requiring disclosure of
  realistic synthetic media) and FTC-style endorsement-disclosure rules for
  paid/sponsored content as non-negotiable, not optional — bans and legal
  exposure both trace back here.
- **Access control:** RBAC on the internal dashboard (who can approve
  publishing, who can see financials/tokens), full audit trail of every
  agent action and every human approval/rejection.
- **Data privacy:** if this ever accepts brand or third-party creator data
  beyond your own accounts, plan for basic GDPR/CCPA handling (data
  export/delete) before it becomes a compliance fire drill.

---

## 13. Cost Estimation (illustrative — validate against current vendor pricing)

| Scale tier | Videos/day | Approx. monthly cost | Primary drivers |
|---|---|---|---|
| MVP (1 niche, 1–2 accounts) | 3–5 | **$400–$900/mo** | LLM calls (low), TTS (low), managed video assembly if used, hosting |
| Growth (3–5 niches) | 15–25 | **$1,500–$3,500/mo** | More TTS/render volume, possible self-hosted Remotion to cut per-video cost, more human review hours |
| Scale (10+ niches, many accounts) | 75–150+ | **$6,000–$15,000+/mo** | Self-hosted rendering infra, higher LLM volume, dedicated infra ops, generative-video experiments if adopted |

Rough **per-video cost** at MVP using templated assembly (not generative
video): $3–$10 (LLM + TTS + managed render + captioning). Generative video
(Runway/Kling-class) can push this to $20–$60+/video depending on length and
provider — a major reason to treat it as a phase-2+ experiment, not the MVP
default. **Do not build a cost model assuming a specific Whop CPM** — get
current live campaign terms first; payout per 1,000 approved views varies
widely by brand/budget and shrinks as more creators join a saturated
campaign. The Revenue Optimization Module (§6) is what replaces these
illustrative estimates with real numbers once Phase 1 is running.

---

## 14. Scalability Plan

- **Horizontal render workers:** rendering is the most parallelizable,
  resource-heavy stage — scale worker pool independently of the
  reasoning/agent services.
- **Template reuse over novel generation:** cache and reuse proven visual/
  caption templates per niche (§3.3); only pay for full LLM+render cost on
  new concepts, not on every variant of a proven winner.
- **Progressive autonomy, not progressive volume-first:** the safest scaling
  axis is *narrowing the human-approval scope per §11's phase table* on
  proven, low-risk niches/patterns — not simply increasing daily video
  count. Volume without a proven authenticity/quality bar is how accounts
  get banned.
- **Account portfolio strategy:** grow by adding independently-operated
  accounts with distinct content styles/niches rather than cloning one
  account's content across many handles (the latter is a duplicate-content/
  inauthentic-behavior flag).
- **Multi-tenant path:** if this becomes a product for other creators
  (rather than just internal use), the schema in §7 is already
  tenant-shaped (`owned_accounts`, `campaigns` etc. all foreign-key
  cleanly to an `organization_id` you'd add) — but treat that as a phase
  3+ decision, not an MVP requirement.

---

## 15. Failure Scenarios & Resilience Playbook

General principle across every scenario below: **fail closed for anything
publish- or spend-related** (pause and wait for a human rather than guess),
**fail open for anything read/analysis-related** (a missed data point is
cheap; an unauthorized publish or continued spend during a problem is not).

### 15.1 No campaign available (niche is dry)

- **Detection:** Research Agent's scan returns zero campaigns meeting the
  niche-fit/ROI threshold for N consecutive cycles.
- **Automated response:** Orchestrator pauses production allocation for
  that niche and the Experimentation Engine (§4) reallocates exploration
  budget to the next-ranked niche — the system doesn't idle, it
  reprioritizes.
- **Human role:** a recurring digest flags "niche X dry for N cycles";
  human decides retire vs. wait out a seasonal cycle.

### 15.2 Video rejected (by the brand/campaign, post-submission)

Distinct from an internal review rejection (§5.1) — this is Whop/brand
disapproving a submitted video.

- **Detection:** Whop dashboard/status shows rejected/disapproved (manual
  check in Phase 1, polled where possible in later phases).
- **Automated response:** log the rejection reason if provided; tag the
  underlying script/pattern in the Content Intelligence Layer as
  `rejected_by_brand` (a distinct tag from internal rejection); halt
  further submissions of that specific pattern to that specific campaign.
- **Human role:** review the reason — if it's a fixable rule
  misunderstanding, feed it back into the Script Agent's guardrails; if
  it's arbitrary or the brand is a bad fit, deprioritize that brand/
  campaign in Research Agent scoring.

### 15.3 Low retention / underperformance after publish

- **Detection:** Analytics Agent checkpoint (e.g., 24h) shows completion
  rate/watch time below the niche baseline.
- **Automated response:** Experimentation Engine marks the specific pattern
  as underperforming (subject to the sample-size guardrail in §3.3 — one
  bad video doesn't retire a pattern); Publishing Agent stops generating
  further variants from that specific idea; no further spend against it.
- **Human role:** only escalated when a *previously confirmed* winning
  pattern starts failing repeatedly — that's a real signal (algorithm
  shift, saturation, execution-quality drift) worth investigating. Isolated
  single misses on new/candidate patterns are expected noise, not incidents.

### 15.4 Platform account restriction / shadowban / suspension

- **Detection:** posting API returns restriction-indicating errors, a
  sudden across-the-board metrics collapse inconsistent with content
  quality, or an explicit platform notice.
- **Automated response:** **immediate hard-stop of all automated
  publishing to that account** — this is the one failure mode where the
  system must fail closed without exception. It does **not** attempt to
  route around the restriction (e.g., spinning up a replacement account) —
  that itself reads as evasion and escalates platform-policy risk.
- **Human role:** mandatory manual review before any resumption — appeal
  if warranted, and a root-cause pass (posting cadence, cross-account
  content similarity, disclosure compliance) that also audits *other*
  accounts sharing infrastructure or content patterns with the restricted
  one, since the same root cause likely affects them too.

### 15.5 API limitations (quota exhaustion, rate limits, provider outage)

- **Detection:** quota-remaining tracking approaching a warning threshold
  (e.g., YouTube's daily unit budget), HTTP 429 responses, provider status
  incidents.
- **Automated response:** queue backpressure — the Publishing Agent defers
  rather than drops work (the durable workflow engine, §9, handles
  retry-with-backoff natively); Orchestrator reprioritizes remaining queued
  items by expected value if only partial quota remains for the day.
- **Human role:** escalated only if an outage persists beyond a defined
  threshold (e.g., >24h) with real business impact, such as an approaching
  campaign deadline.

---

## 16. 90-Day Roadmap

**Phase 1 — Days 1–30: Prove one profitable, policy-safe pipeline**
- Confirm Whop Content Rewards access model directly with Whop (API vs.
  manual) — this blocks everything else, resolve it first.
- Pick 1 niche, 1–2 owned accounts.
- Build: Research Agent (manual-assisted), Script Agent, templated Video
  Production Agent (Remotion/Whisper/ElevenLabs), manual publishing,
  manual metrics entry, basic viral-score calculation, manual cost
  tracking (Revenue Optimization Module in spreadsheet form).
- Human review gate on 100% of output (§11 Phase 1 column).
- Goal: 15–20 published videos, at least one repeatable winning pattern
  identified, first real Whop payout data collected.

**Phase 2 — Days 31–60: Automate the loop, add guardrails**
- Automate publishing via platform APIs (start TikTok/YouTube app-review
  processes early — they take weeks).
- Automate metrics ingestion via APIs where available.
- Stand up the Content Intelligence Layer for real (hook database, pattern
  storage, competitor tracking) and wire Script Agent retrieval to it.
- Turn on the Experimentation Engine in recommend-only mode; automate the
  Revenue Optimization Module's profit-per-video dashboard.
- Add budget governor and anomaly detection (Analytics Agent).
- Expand to 3–5 niches, narrowing human review scope per §11 Phase 2.
- Goal: positive ROI demonstrated on at least 2 niches; internal dashboard
  live for pipeline and profit visibility.

**Phase 3 — Days 61–90: Controlled scale**
- Narrow the review-gate scope further on proven, low-risk niches/patterns
  per §11 Phase 3 (never to zero — sampled audits stay permanent).
- Expand account portfolio with distinct styles per niche.
- Evaluate generative-video pilot for 1 niche where it fits.
- Let the Experimentation Engine act automatically within governor bounds.
- Formalize cost-per-video optimization (template reuse, self-hosted
  render if managed costs are the bottleneck).
- Decide: stay internal-only, or begin multi-tenant productization.
- Goal: repeatable, documented playbook per niche; system runs with
  reduced daily human intervention while staying within a defined
  review-sampling rate.

---

## 17. MVP Definition

**In scope:**
- 1 niche, 1–2 owned/authorized accounts, manual-assisted campaign
  discovery.
- Research Agent producing a written competitor/trend brief (LLM-driven,
  human-supplied raw data).
- Script Agent producing 3 variants per concept.
- Templated video production (stock/b-roll + TTS + Whisper captions), no
  generative video.
- Mandatory human review before every publish, using the review mechanics
  in §5.1.
- Manual or semi-automated publishing to 1–2 platforms.
- Manual/semi-automated metrics collection feeding a basic viral-score
  calculation.
- Manual cost tracking (even spreadsheet-level) so profit-per-video is
  known from day one, not just view counts.
- A minimal internal dashboard (even a spreadsheet-backed one) showing
  pipeline state.

**Explicitly out of scope for MVP:** fully autonomous publishing, generative
video, multi-tenant support, large account portfolios, reduced-review
autonomy, an acting (vs. data-collecting) Experimentation Engine. These are
earned by Phase 2/3 evidence per §11, not assumed upfront.

**Success criteria:** 15–20 videos published in the first 30 days, at least
one content pattern with a positive, repeatable ROI signal, zero platform
policy strikes, and a confirmed answer on how Whop Content Rewards
integration actually works end-to-end.

---

## Open Questions for You (need answers before Phase 1 build starts)

1. Do you already have a Whop Content Rewards partner/API relationship, or
   does discovery start from the public dashboard?
2. Which niche(s) do you want to start with — do you already have a
   campaign in mind, or should Research Agent design start from scratch?
3. Do you have existing TikTok/YouTube/Instagram accounts to use, or does
   account creation/warmup need to be part of Phase 1?
4. Any hard budget ceiling for the first 30 days (LLM/API/tooling spend)?

---

*This document is a design artifact only. No production code has been
written. Proceed to implementation only after explicit approval.*
