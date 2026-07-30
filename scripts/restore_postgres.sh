#!/usr/bin/env bash
# Self-hosted Postgres restore script (Production Hardening Sprint H3) —
# the counterpart to backup_postgres.sh. See docs/DATABASE_OPERATIONS.md
# for the full restore runbook (including the drill you should actually
# run before you ever need this for real).
#
# Restores into the target database named in DATABASE_URL. Refuses to run
# against a database that already has tables, unless FORCE=1 is set — the
# whole point of a restore script is to not be the thing that accidentally
# destroys a live database.
#
# Usage:
#   DATABASE_URL=postgresql://user:pass@host:5432/dbname_to_restore_into \
#     ./scripts/restore_postgres.sh /path/to/content_factory_TIMESTAMP.dump

set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
ARCHIVE_PATH="${1:?Usage: restore_postgres.sh <path-to-.dump-file>}"

if [[ ! -f "${ARCHIVE_PATH}" ]]; then
  echo "Archive not found: ${ARCHIVE_PATH}" >&2
  exit 1
fi

EXISTING_TABLES="$(psql "${DATABASE_URL}" -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"

if [[ "${EXISTING_TABLES}" != "0" && "${FORCE:-0}" != "1" ]]; then
  echo "Refusing to restore: target database already has ${EXISTING_TABLES} table(s)." >&2
  echo "Set FORCE=1 if you really mean to restore over an existing database" \
       "(pg_restore --clean below will drop/recreate conflicting objects)." >&2
  exit 1
fi

echo "Restoring ${ARCHIVE_PATH} into ${DATABASE_URL%%@*}@..."
pg_restore --clean --if-exists --no-owner --dbname="${DATABASE_URL}" "${ARCHIVE_PATH}"

echo "Restore complete. Verifying with a basic row-count sanity check..."
psql "${DATABASE_URL}" -c "
  SELECT relname AS table_name, n_live_tup AS approx_row_count
  FROM pg_stat_user_tables
  ORDER BY relname;
"

echo "Restore finished — now run 'alembic current' and compare against the" \
     "expected revision, and spot-check the application against this" \
     "database before pointing real traffic at it."
