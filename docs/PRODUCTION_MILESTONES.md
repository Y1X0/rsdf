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
