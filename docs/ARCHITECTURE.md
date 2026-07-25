# AI Content Factory — Architecture & Strategy Document

**System:** Automated content pipeline for Whop Content Rewards campaigns
**Status:** Design phase — no implementation yet
**Version:** 0.1 (draft for approval)

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
philosophy drives the roadmap in §10.

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
                                              back to Research/Script
                                              (learned patterns, RAG store)

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
  worked" instead of hallucinating trends.
- **Guardrails:** every agent that touches money, publishing, or brand
  claims has a hard-coded validation step (schema checks, banned-word/claim
  filters, budget ceilings) that runs in code, not just LLM judgment.

### 2.1 Research Agent

- **Inputs:** Whop campaign listings (scraped/semi-manual feed), TikTok
  Creative Center trend data, public competitor posts, Google Trends,
  historical internal performance data.
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

- **Inputs:** approved campaign brief, niche research, retrieved
  high-performing hook/script patterns from vector memory.
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
  channels) — see §5 and §10.
- **Guardrail:** automatic AI-disclosure tagging metadata attached to any
  video containing synthetic voice/visuals, so the Publishing Agent can
  apply the correct platform label.

### 2.4 Publishing Agent

- **Inputs:** approved, rendered video + metadata.
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
  budgets (see §4) and blocks duplicate/near-duplicate content across
  accounts to avoid inauthentic-behavior detection.

### 2.5 Analytics Agent

- **Inputs:** platform analytics APIs (views, average watch time,
  completion, shares, comments, likes, saves where available), Whop
  earnings data.
- **Outputs:** `viral_score` per video using the specified weighting,
  earnings-per-video, and a `duplicate | iterate | retire` recommendation
  per content pattern.
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
  if realized ROI falls below threshold), and is the only agent allowed to
  transition content out of the human review gate.
- **Not an LLM free-for-all:** implemented primarily as deterministic
  workflow code (e.g., Temporal.io workflow) that *calls* LLM agents as
  steps, rather than an LLM deciding control flow — control flow needs to be
  debuggable and replayable, which agentic loops are bad at.

---

## 3. Database Design (relational core, Postgres)

```
campaigns
  id, whop_campaign_id, brand_name, niche_id, payout_model, cpm_rate,
  budget_cap, budget_remaining_est, rules_json, ai_content_allowed (bool),
  discovered_at, expires_at, status, roi_score

niches
  id, name, category, saturation_score, avg_cpm_est, trend_score, updated_at

tracked_accounts (competitors, for research)
  id, platform, handle, niche_id, follower_count, avg_views_est, last_scraped_at

content_ideas
  id, campaign_id, concept_summary, predicted_score, source ('research_agent'),
  status ('proposed'|'approved'|'rejected'), created_at

scripts
  id, idea_id, variant_label, hook_text, full_text, cta_text, target_duration_s,
  generated_by_model, created_at

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

learning_patterns
  id, pattern_type ('hook'|'structure'|'niche'|'timing'), description,
  supporting_publication_ids (array), confidence_score, created_at

agent_runs (audit log)
  id, agent_name, campaign_id, input_ref, output_ref, model_used,
  cost_usd, latency_ms, status, created_at
```

Supplementary stores:

- **Vector store** (pgvector to start, dedicated store like Pinecone/
  Weaviate only if scale demands it): embeddings of hooks/scripts/
  transcripts tagged with outcome, used for retrieval-augmented generation
  in the Research/Script agents.
- **Object storage** (S3 or Cloudflare R2): raw footage, rendered video/audio,
  thumbnails.
- **Redis:** job queue backing, rate-limit counters per account/platform,
  short-TTL caches.

---

## 4. API Integrations Needed

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

## 5. Recommended Technology Stack

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
| Internal dashboard | Next.js + Tailwind | Review gate UI, campaign pipeline visibility, analytics |
| Hosting (MVP) | Fly.io or Railway | Fast iteration, low ops overhead |
| Hosting (scale) | AWS (ECS/EKS) | When render/queue volume justifies the ops cost |
| Observability | Sentry (errors), Grafana+Prometheus (metrics), PostHog (product analytics) | Standard, well-supported |
| CI/CD | GitHub Actions | Matches repo hosting |

---

## 6. Automation Workflow (end-to-end)

1. **Discover:** Research Agent surfaces candidate campaigns (semi-manual
   feed initially) → scores niche/saturation/payout → Orchestrator applies
   go/no-go, rejecting anything whose rules forbid AI content.
2. **Ideate & script:** Script Agent retrieves winning patterns from vector
   memory, generates 3–5 variants → automated brand-safety/claim filter →
   queued for human glance-review (fast, not frame-by-frame) if this is a
   new niche/account, auto-approved once the pattern has a track record.
3. **Produce:** Video Production Agent renders each variant → automated QC
   (duration, audio sync, caption accuracy) → **human review gate**
   (mandatory at MVP; becomes spot-check sampling later) for brand safety,
   authenticity, and platform-policy risk.
4. **Publish:** Publishing Agent schedules approved videos across the
   correct platform(s)/account(s), respecting per-account human-cadence
   rate limits, applies AI-disclosure and branded-content labels, avoids
   near-duplicate cross-posting patterns.
5. **Track:** Analytics Agent polls metrics on a schedule (e.g., 1h, 6h,
   24h, 72h, 7d checkpoints) and Whop earnings data as it settles.
6. **Score & learn:** Viral score computed at checkpoints → `duplicate /
   iterate / retire` decision → winning patterns written back to vector
   memory with outcome tags → next Script Agent run retrieves them.
7. **Governor check:** Orchestrator continuously compares realized
   payout/earnings vs. production+API cost per niche/campaign; auto-pauses
   a niche or campaign that's unprofitable after a defined sample size.

---

## 7. Security Considerations

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

## 8. Cost Estimation (illustrative — validate against current vendor pricing)

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
campaign.

---

## 9. Scalability Plan

- **Horizontal render workers:** rendering is the most parallelizable,
  resource-heavy stage — scale worker pool independently of the
  reasoning/agent services.
- **Template reuse over novel generation:** cache and reuse proven visual/
  caption templates per niche; only pay for full LLM+render cost on new
  concepts, not on every variant of a proven winner.
- **Progressive autonomy, not progressive volume-first:** the safest scaling
  axis is *reducing human review percentage* on proven, low-risk
  niches/patterns — not simply increasing daily video count. Volume without
  a proven authenticity/quality bar is how accounts get banned.
- **Account portfolio strategy:** grow by adding independently-operated
  accounts with distinct content styles/niches rather than cloning one
  account's content across many handles (the latter is a duplicate-content/
  inauthentic-behavior flag).
- **Multi-tenant path:** if this becomes a product for other creators
  (rather than just internal use), the schema above is already
  tenant-shaped (`owned_accounts`, `campaigns` etc. all foreign-key
  cleanly to an `organization_id` you'd add) — but treat that as a phase
  3+ decision, not an MVP requirement.

---

## 10. 90-Day Roadmap

**Phase 1 — Days 1–30: Prove one profitable, policy-safe pipeline**
- Confirm Whop Content Rewards access model directly with Whop (API vs.
  manual) — this blocks everything else, resolve it first.
- Pick 1 niche, 1–2 owned accounts.
- Build: Research Agent (manual-assisted), Script Agent, templated Video
  Production Agent (Remotion/Whisper/ElevenLabs), manual publishing,
  manual metrics entry, basic viral-score calculation.
- Human review gate on 100% of output.
- Goal: 15–20 published videos, at least one repeatable winning pattern
  identified, first real Whop payout data collected.

**Phase 2 — Days 31–60: Automate the loop, add guardrails**
- Automate publishing via platform APIs (start TikTok/YouTube app-review
  processes early — they take weeks).
- Automate metrics ingestion via APIs where available.
- Build the vector-memory feedback loop (learning_patterns → Script Agent
  retrieval).
- Add budget governor and anomaly detection (Analytics Agent).
- Expand to 3–5 niches, still with human review gate but moving toward
  spot-checking on proven patterns.
- Goal: positive ROI demonstrated on at least 2 niches; internal dashboard
  live for pipeline visibility.

**Phase 3 — Days 61–90: Controlled scale**
- Reduce review-gate percentage on proven, low-risk niches.
- Expand account portfolio with distinct styles per niche.
- Evaluate generative-video pilot for 1 niche where it fits.
- Formalize cost-per-video optimization (template reuse, self-hosted
  render if managed costs are the bottleneck).
- Decide: stay internal-only, or begin multi-tenant productization.
- Goal: repeatable, documented playbook per niche; system runs with
  reduced daily human intervention while staying within a defined
  review-sampling rate.

---

## 11. MVP Definition

**In scope:**
- 1 niche, 1–2 owned/authorized accounts, manual-assisted campaign
  discovery.
- Research Agent producing a written competitor/trend brief (LLM-driven,
  human-supplied raw data).
- Script Agent producing 3 variants per concept.
- Templated video production (stock/b-roll + TTS + Whisper captions), no
  generative video.
- Mandatory human review before every publish.
- Manual or semi-automated publishing to 1–2 platforms.
- Manual/semi-automated metrics collection feeding a basic viral-score
  calculation.
- A minimal internal dashboard (even a spreadsheet-backed one) showing
  pipeline state.

**Explicitly out of scope for MVP:** fully autonomous publishing, generative
video, multi-tenant support, large account portfolios, reduced-review
autonomy. These are earned by Phase 2/3 evidence, not assumed upfront.

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
