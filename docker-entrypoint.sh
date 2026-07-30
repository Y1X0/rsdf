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

# PORT defaults to 8000 (docker-compose.yml, plain `docker run` with no
# PORT set) but is honored when the platform injects one — required for
# Render's Docker web services, which assign their own PORT and expect the
# container to bind to it rather than a fixed value.
case "$1" in
  migrate)
    exec alembic upgrade head
    ;;
  serve|"")
    # Opt-in escape hatch for platforms that can't run a one-off `migrate`
    # command against a web service (e.g. Render's free/starter web
    # services expect the container to stay up and bind $PORT; a command
    # that runs alembic and exits gets treated as a crashed deploy, not a
    # completed job - preDeployCommand or a separate one-off Job is the
    # right tool there, but isn't available on every plan). Defaults off
    # to preserve the documented multi-replica-safe sequencing above; only
    # set RUN_MIGRATIONS_ON_START=true for a single-instance deployment.
    # `set -e` means a failed migration aborts here instead of serving
    # against a stale schema.
    if [ "${RUN_MIGRATIONS_ON_START:-false}" = "true" ]; then
      alembic upgrade head
    fi
    exec uvicorn content_factory.api.main:app \
      --host 0.0.0.0 \
      --port "${PORT:-8000}" \
      --workers "${WEB_CONCURRENCY:-1}"
    ;;
  *)
    exec "$@"
    ;;
esac
