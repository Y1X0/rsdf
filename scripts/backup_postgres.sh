#!/usr/bin/env bash
# Self-hosted Postgres backup script (Production Hardening Sprint H3).
#
# This is the fallback path — see docs/DATABASE_OPERATIONS.md's strong
# recommendation to use a managed Postgres service (RDS, Cloud SQL,
# Supabase, etc.) with built-in automated backups and point-in-time
# recovery instead of this script wherever that's an option. Use this only
# if self-hosting Postgres is a deliberate choice.
#
# Produces a single custom-format (`pg_dump -Fc`) archive, which supports
# selective/parallel restore and is significantly smaller than plain SQL
# dumps. Uploads to S3-compatible storage if AWS_* / BACKUP_S3_BUCKET are
# set; always keeps a local copy under BACKUP_DIR regardless, pruned to
# BACKUP_RETENTION_DAYS.
#
# Usage:
#   DATABASE_URL=postgresql://user:pass@host:5432/dbname \
#   BACKUP_DIR=/var/backups/content-factory \
#   BACKUP_RETENTION_DAYS=30 \
#   BACKUP_S3_BUCKET=my-backups-bucket \
#   BACKUP_S3_PREFIX=content-factory/postgres \
#     ./scripts/backup_postgres.sh
#
# Intended to run on a schedule (cron, a Kubernetes CronJob, etc.) — see
# docs/DATABASE_OPERATIONS.md for the recommended schedule and retention
# policy this script's defaults match.

set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/content-factory}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE_NAME="content_factory_${TIMESTAMP}.dump"

mkdir -p "${BACKUP_DIR}"
ARCHIVE_PATH="${BACKUP_DIR}/${ARCHIVE_NAME}"

echo "Backing up ${DATABASE_URL%%@*}@... to ${ARCHIVE_PATH}"
pg_dump --format=custom --file="${ARCHIVE_PATH}" --dbname="${DATABASE_URL}"
echo "Backup written: $(du -h "${ARCHIVE_PATH}" | cut -f1)"

if [[ -n "${BACKUP_S3_BUCKET:-}" ]]; then
  S3_KEY="${BACKUP_S3_PREFIX:-content-factory/postgres}/${ARCHIVE_NAME}"
  echo "Uploading to s3://${BACKUP_S3_BUCKET}/${S3_KEY}"
  aws s3 cp "${ARCHIVE_PATH}" "s3://${BACKUP_S3_BUCKET}/${S3_KEY}"
fi

echo "Pruning local backups older than ${BACKUP_RETENTION_DAYS} days"
find "${BACKUP_DIR}" -name "content_factory_*.dump" -mtime "+${BACKUP_RETENTION_DAYS}" -print -delete

echo "Backup complete: ${ARCHIVE_NAME}"
