#!/bin/sh
# Explicit migrate-vs-serve dispatch (Production Hardening Sprint H1,
# closing PHASE1_AUDIT_v2.md-style finding DP2 — "no documented
# migration-on-deploy strategy"). Migrations are never run implicitly as a
# side effect of starting the app: a real deploy runs
# `docker run ... migrate` (or the docker-compose `migrate` service) once,
# to completion, *before* rolling out the new `serve` replicas — see
# docs/DEPLOYMENT.md for the full sequence. Running migrations from every
# replica's own startup would race multiple copies against each other on
# any multi-replica deploy.
set -e

case "$1" in
  migrate)
    exec alembic upgrade head
    ;;
  serve|"")
    exec uvicorn content_factory.api.main:app \
      --host 0.0.0.0 \
      --port 8000 \
      --workers "${WEB_CONCURRENCY:-1}"
    ;;
  *)
    exec "$@"
    ;;
esac
