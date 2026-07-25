# Database Operations

Written for the Production Hardening Sprint (item 3, "Database Production
Safety"), closing `docs/PRODUCTION_READINESS_REVIEW.md`'s DR1-DR4 findings
(no backup strategy at all existed before this document and the scripts it
describes). Covers: managed Postgres setup, automated backups, the restore
procedure, retention policy, and rendered-media backup.

## 1. Managed Postgres, not self-hosted, is the recommendation

**Use a managed Postgres service** (AWS RDS, Google Cloud SQL, Supabase,
Neon, Render's managed Postgres, etc.) for any real deployment. All of
these provide, essentially for free:

- Automated daily backups with configurable retention.
- Point-in-time recovery (PITR) via WAL archiving — restore to "5 minutes
  before the bad migration," not just "last night."
- Automated minor-version patching and (on most) automated failover.

This is a stronger, less error-prone starting point than self-hosting
Postgres and reimplementing all of the above by hand. `scripts/
backup_postgres.sh`/`restore_postgres.sh` (below) exist as the fallback
for anyone who has a real reason to self-host — they are **not** a
substitute for the managed-service PITR guarantee, since a `pg_dump`-based
approach only ever gets you back to the last completed dump, never to an
arbitrary point in between.

**Minimum viable production setup, in order of preference:**

1. A managed Postgres instance, in the same region as the app, with
   automated backups and PITR enabled at instance-creation time (this is
   usually a checkbox, not extra work).
2. `DATABASE_URL` pointed at it, `ENVIRONMENT=production` set (this fails
   the app closed at startup if `DATABASE_URL` is still SQLite — see
   `config.py::Settings.validate_production_safety`).
3. Connect over TLS (`?sslmode=require` or your provider's equivalent) —
   verify this is the default for your provider; some require it
   explicitly in the connection string.
4. Restrict network access to the app's own network (VPC peering,
   security groups, or your provider's private-networking feature) —
   never expose the database on the public internet.

## 2. Self-hosted fallback: `scripts/backup_postgres.sh` / `restore_postgres.sh`

Both scripts were written and **verified against a real local Postgres 16
instance** as part of this sprint (a full dump → fresh-database restore →
row-count verification round-trip, plus confirming the restore script
refuses to overwrite a database that already has tables without an
explicit `FORCE=1`).

```bash
# Backup (run on a schedule — see §4 for the recommended cadence)
DATABASE_URL=postgresql://user:pass@host:5432/content_factory \
BACKUP_DIR=/var/backups/content-factory \
BACKUP_RETENTION_DAYS=30 \
BACKUP_S3_BUCKET=my-backups-bucket \
  ./scripts/backup_postgres.sh

# Restore (into a fresh/target database)
DATABASE_URL=postgresql://user:pass@host:5432/target_db \
  ./scripts/restore_postgres.sh /var/backups/content-factory/content_factory_20260101T000000Z.dump
```

Produces a `pg_dump -Fc` (custom format) archive — smaller than plain SQL,
supports selective/parallel restore via `pg_restore`. Uploads to S3 if
`BACKUP_S3_BUCKET` is set (via the `aws` CLI — install and configure it
separately, this script doesn't manage AWS credentials itself); always
keeps a local copy, pruned to `BACKUP_RETENTION_DAYS`.

Run `backup_postgres.sh` on a schedule via cron, a Kubernetes `CronJob`, or
your platform's scheduled-task equivalent — nothing in this repository
runs it automatically.

## 3. Restore runbook

**Do this drill before you ever need it for real** — an untested backup is
an unproven one, and discovering it was misconfigured is exactly the
moment you can least afford that.

1. Provision a fresh, empty database (never restore over a live one as
   your first attempt at this).
2. `DATABASE_URL=<fresh-db-url> ./scripts/restore_postgres.sh <archive path>`
3. Confirm the script's own row-count summary looks sane (non-zero counts
   on tables you know have data).
4. `alembic current` against the restored database and compare against the
   revision you expect (the tip of `alembic/versions/` at backup time).
5. Point a local copy of the app at the restored database
   (`DATABASE_URL=<fresh-db-url>`) and smoke-test a handful of read
   endpoints (`GET /campaigns`, `GET /videos`, `GET /dashboard/summary`)
   before ever considering the restore verified.
6. Only after all of the above: if this is a real incident recovery (not a
   drill), repoint the real `DATABASE_URL` and restart the app.

If step 2 needs to restore *over* an existing (e.g., corrupted) database
rather than into a fresh one, set `FORCE=1` — the script otherwise refuses,
on purpose (verified: it correctly declined a restore attempt against a
database that already had 26 tables during this sprint's testing).

## 4. Backup retention policy

| Tier | Frequency | Retention | Rationale |
|---|---|---|---|
| Daily | Every 24h | 30 days | Covers "someone notices a data problem within a month" — the overwhelming majority of real incidents. |
| Weekly | Every 7 days | 90 days | Covers slower-to-notice issues (a subtly wrong migration, a bad data-quality bug) without keeping 90 daily copies. |
| Monthly | 1st of the month | 1 year | Compliance/audit-trail-adjacent long tail; cheap to keep, rarely needed. |

Most managed Postgres services implement exactly this tiering
automatically once daily backups + PITR are enabled — configure it there
rather than reimplementing tiering in `backup_postgres.sh`, which
deliberately stays simple (one retention window) for the self-hosted
fallback case. If self-hosting and you want the full tiering above, run
the script three times with three different `BACKUP_DIR`/
`BACKUP_RETENTION_DAYS` values (or three separate cron schedules writing
into differently-prefixed S3 keys).

## 5. Media backup (rendered video/audio assets)

Separate from the database entirely — `MEDIA_STORAGE_DIR` (rendered
video/audio) has its own, independent durability story, added in this
sprint (`services/media_backup.py`):

- **Default (`MEDIA_BACKUP_ENABLED=false`):** no backup, matching Phase
  1/2's existing behavior exactly. Local disk is the only copy.
- **Enabled (`MEDIA_BACKUP_ENABLED=true` + `MEDIA_BACKUP_S3_BUCKET`):**
  every rendered video and synthesized audio file also gets a best-effort
  copy uploaded to S3 (needs the `storage` extra —
  `pip install '.[storage]'`), immediately after each asset is produced
  (`services/production_service.py::render_video`). This is deliberately
  **best-effort and non-fatal** — a failed backup logs a warning and the
  render still succeeds; losing the backup copy of a video that already
  rendered successfully must never be the reason the request fails.
- **This is a backup, not a storage migration.** Local disk remains the
  primary read path everywhere in the codebase (`Video.asset_url`, the TTS
  `audio_path`, etc. are completely unchanged) — recovering from a lost
  local volume means restoring from the S3 backup copies back to local
  disk (or, as a larger follow-up beyond this sprint's scope, actually
  serving reads from S3 directly — see
  `docs/PRODUCTION_HARDENING_REPORT.md`'s remaining-risks section for why
  that's a bigger change deliberately left for later).
- AWS credentials come from boto3's own standard environment variables
  (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION`), not a
  custom setting — this matches how the AWS CLI itself is configured, so
  ops tooling and the app agree on where credentials live.
