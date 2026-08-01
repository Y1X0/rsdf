# Production Milestones

Real, verified milestones reached on the live deployment
(`https://content-factory-bhhd.onrender.com`) — each entry is evidence-backed
(a real GitHub Actions run, not a claim), matching this project's standing
rule of never marking something done without an actual verified result.

## 2026-07-30 — First fully-automated, real Instagram publish

**Commit:** `b62010f868522ba3849ee4f3b6db0ed6b85ef58c` (main, PR #8 merged)
**Verification run:** [`verify-production.yml` #30553797276](https://github.com/Y1X0/rsdf/actions/runs/30553797276)

The full Clip Factory pipeline ran end-to-end against the live deployment,
with every stage real (no placeholders, no manual steps), ending in a real,
automated publish to Instagram:

```
auto_publish_status: published
auto_publish_detail: Publication #4 via account #1 (instagram): published

Overall: PASS - the full pipeline produced a real clip end-to-end
         on this deployment.
```

Full stage sequence, all real:

1. Uploaded a real long-form source video.
2. Groq Whisper produced a real transcript.
3. Groq LLM selected a real, hooked clip.
4. Real ffmpeg render (9:16, hook overlay, karaoke captions) — confirmed a
   real, playable `.mp4` via `ffprobe` (real video + audio streams).
5. The rendered asset got a real, publicly-fetchable `https://` URL via this
   app's own `/public-media` route (`services/media_backup.py`'s
   `LocalDiskMediaBackupProvider` — no external cloud storage account
   needed).
6. Human review approval triggered the auto-publish cascade with zero
   manual calls.
7. The real Instagram Graph API (Instagram API with Instagram Login,
   `graph.instagram.com`) accepted and published the media.

**Known, separate, non-blocking issue as of this milestone:** the
auto-metrics-sync step (`GET /insights`) returns a `400` against
`graph.instagram.com` — does not affect publishing, tracked separately
(see the repository's open issues).

**What this proves:** the pipeline is a real, working, end-to-end
AI-driven content production and publishing system on this deployment —
not a demo. Follow-on work (dashboard/monitoring, account management,
scheduling, deeper analytics, clip-selection quality, additional
platforms) builds on top of a working foundation, not toward one.

## 2026-08-01 — Production validation: Render Free ephemeral storage tested

**Commit:** `0e7bb2ca61002b65180f851756a80f1577c7c06d` (main, PR #20 merged)
**Verification runs:**
- [`verify-source-video-pipeline.yml` #30697534684](https://github.com/Y1X0/rsdf/actions/runs/30697534684) (source_video_id 29 — transcribe/analyze/render)
- [`sync-content-rewards.yml` #30698534574](https://github.com/Y1X0/rsdf/actions/runs/30698534574) (missing-file recovery)
- [`sync-content-rewards.yml` #30698726655](https://github.com/Y1X0/rsdf/actions/runs/30698726655) (no-op when file already present)

This service runs on Render's free tier, which has no persistent Disk: an
idle instance spin-down (or a redeploy) hands the next request a brand-new,
empty filesystem at any time, independent of any code change. This was a
real, live-discovered failure mode — not a hypothetical — first surfacing as
an unhandled `FileNotFoundError` inside the Groq Whisper provider when
`/transcribe` was called on a `SourceVideo` whose file had been wiped by a
spin-down. Rather than add paid persistent storage, the system was made
self-healing: any pipeline stage that needs a `SourceVideo`'s file first
calls `services/media_availability.py::ensure_local_media_available()`,
which re-fetches the file from its original source (Content Rewards →
Google Drive) into the exact same database row if it's missing, and is a
no-op if the file is already there.

Production Validation: Render Free ephemeral storage tested. Missing local
media recovery verified: Content Rewards → Google Drive → transcribe →
analyze → render.

Evidence, both directions, from real production runs (not unit tests):

```
# Cold container, files missing -> all three re-fetched
{"external_id":"efa2656a-...","source_video_id":26,"created":true}
{"external_id":"61703a2f-...","source_video_id":28,"created":true}
{"external_id":"1df38301-...","source_video_id":29,"created":true}

# Same warm container, seconds later, files now present -> zero re-fetches
{"external_id":"efa2656a-...","source_video_id":26,"created":false}
{"external_id":"61703a2f-...","source_video_id":28,"created":false}
{"external_id":"1df38301-...","source_video_id":29,"created":false}
```

And, on the same deployment, source_video_id 29's full downstream pipeline
completing for real after a recovery:

```
transcription: PASS (real Groq Whisper transcript)
clip_selection: PASS (real Groq LLM clip suggestion)
render: PASS (real ffmpeg .mp4, qc_status=passed)
```

(That run's `publish` stage returned `auto_publish_status: skipped` —
`"Account has already published 1/1 today"` — a legitimate daily-publish-limit
business rule, not a defect.)

**What this proves:** the system no longer depends on Render persisting
files locally — the biggest architectural risk in running this on a free
plan. A spin-down/restart can no longer turn into a 500 or a stuck pipeline
for any `CONTENT_REWARDS`-sourced video; the file is recovered on demand,
reusing the same row, with no impact on the `source_videos` uniqueness
guarantee.
