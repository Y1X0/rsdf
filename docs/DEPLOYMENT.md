# Deployment Guide

Written as part of the Production Hardening Sprint (see
`docs/PRODUCTION_READINESS_REVIEW.md` for the audit that motivated it and
`docs/PRODUCTION_HARDENING_REPORT.md` for the full list of what changed).
This document covers *how to actually run this system* — local
production-like testing via Docker Compose, and the real deployment
sequence. It assumes the reader has already read `docs/PHASE1.md` for what
the system does.

## 1. Configuration is entirely environment variables

Every setting lives in `content_factory.config.Settings` (a
`pydantic-settings` `BaseSettings`) and is read from real process
environment variables — see `.env.example` for the full, documented list.
There is **no other configuration mechanism**: no config file format, no
command-line flags, no hardcoded environment-specific branches in code
(with one exception, see §2). Locally, a `.env` file in the repo root is
read as a fallback for anything not already set in the real environment
(`SettingsConfigDict(env_file=".env")`) — this is a local-development
convenience only; a real deployment should set real environment variables
(via your platform's secret manager, Kubernetes `Secret`/`ConfigMap`,
`docker run -e`, etc.), not ship a `.env` file inside an image or repo.

**Never commit a real `.env` file** — it's already `.gitignore`d.

## 2. The one environment-aware behavior: `ENVIRONMENT=production`

Setting `ENVIRONMENT=production` activates exactly one thing:
`Settings.validate_production_safety()`, called once at process startup
(`api/main.py::create_app`). It fails closed (refuses to start, with a
clear error) if:

- `DATABASE_URL` points at SQLite (the safe local-dev/test fallback) —
  production requires a real Postgres instance.
- `JWT_SECRET_KEY` is unset or shorter than 32 characters.

Every other environment (including the unset default, `"development"`)
skips this check entirely — it exists specifically to catch the two
silent-data-loss/silent-insecurity footguns identified in the production
readiness review, not to gate any other behavior.

## 3. Building the image

```bash
docker build -t content-factory:latest .
```

This is a multi-stage build (see `Dockerfile`): dependencies install from
the pinned `requirements-lock.txt` (not `pyproject.toml`'s intentionally
loose bounds — see that file's own header for why and how to regenerate
it) into an isolated venv in the builder stage, which the runtime stage
copies verbatim. The runtime image runs as a non-root user, has no
compiler/build headers, and declares a `HEALTHCHECK` against the app's own
`/health` endpoint.

**Does not include** the optional `rendering` (Pillow/ffmpeg) or
`elevenlabs` extras by default — this image runs with `NullRenderer`/
`SilentTTSProvider` unless rebuilt with those extras added (see the
Dockerfile's own comment for the one-line change).

## 4. Local production-like environment (Docker Compose)

```bash
cp .env.example .env        # fill in real values — see .env.example's comments
docker compose build
docker compose run --rm migrate   # apply migrations — see §5, never automatic
docker compose up -d app
curl http://localhost:8000/health
```

This brings up the app talking to a real Postgres 16 and a real Redis 7 —
not the SQLite/in-process fallbacks that exist for local dev/tests only —
so you're exercising the same topology (and, once H4's Redis-backed rate
limiter is configured, the same multi-worker-safe behavior) that a real
deployment uses. It is **not** a substitute for managed Postgres with real
backups in actual production — see `docs/DATABASE_OPERATIONS.md`.

## 5. Migrations are never run implicitly

`docker-entrypoint.sh` dispatches on its first argument: `migrate` runs
`alembic upgrade head` to completion and exits; `serve` (the default `CMD`)
starts `uvicorn` and never touches migrations. This is deliberate — see the
entrypoint script's own comment — running migrations as a side effect of
every replica's own startup would race multiple copies against each other
the moment you run more than one.

**The real deployment sequence is:**

1. Build and push the new image.
2. Run the `migrate` command **once**, to completion, against the target
   database (`docker compose run --rm migrate` locally; a one-off task/Job
   in whatever orchestrator you use in real production — a Kubernetes
   `Job`, an ECS one-off task, a Render/Railway "release command", etc.).
3. Only after step 2 succeeds, roll out the new `serve` replicas.
4. Roll back by re-running step 2 with the previous version's migration
   target (`alembic downgrade <revision>`) *before* rolling the app back,
   if the new migration must be undone — every migration in this project
   has a real, tested `downgrade()` (verified via a full
   `base → head → base → head` round-trip as part of both this sprint and
   the original production readiness review).

## 6. Worker count and the distributed-safety prerequisite

`WEB_CONCURRENCY` (default `1`) controls how many `uvicorn` worker
processes the container runs. **Do not raise this above `1` without also
setting `RATE_LIMIT_BACKEND=redis` and a real `REDIS_URL`** — the
production readiness review found the in-process rate limiter becomes
`configured_limit × worker_count` under multiple workers, silently. The
Redis-backed limiter (Production Hardening Sprint H4,
`auth/rate_limiter_factory.py`) closes this; `docker-compose.yml`'s `app`
service is already configured to use it (`RATE_LIMIT_BACKEND: redis`,
`WEB_CONCURRENCY: "2"`) as the reference example.

## 7. Environment separation

Nothing in this codebase enforces dev/staging/production separation beyond
"use different environment variables" — there's no built-in multi-tenancy
or per-environment config profile. In practice this means:

- Use **entirely separate** `DATABASE_URL`, `JWT_SECRET_KEY`,
  `TOKEN_ENCRYPTION_KEY`, and platform credentials per environment — never
  share a JWT secret or encryption key between staging and production.
- Name resources (databases, buckets, Redis instances) so a config mistake
  is loud, not silent (e.g. `content-factory-staging` vs.
  `content-factory-prod`, not the same name in two accounts).
- If you adopt infrastructure-as-code, encode this separation there rather
  than relying on operators remembering it by hand.

## 8. Media storage

`MEDIA_STORAGE_DIR` (default `./var/media`) is local-filesystem storage for
rendered video/audio assets. In `docker-compose.yml` this is a named
volume (`media_data`), which survives `docker compose down` but is still
**local to one host** — it does not survive moving to a new host, does not
scale across replicas, and has no backup story of its own. See
`docs/DATABASE_OPERATIONS.md` §"Media backup" for the S3 backup-copy
mechanism added in this sprint (`services/media_backup.py`) — it is a
**backup**, not a storage migration: local disk remains the primary read
path. A full migration to object storage as the *primary* store is a
larger follow-up beyond this sprint's scope (see
`docs/PRODUCTION_HARDENING_REPORT.md`'s remaining-risks section).

## 9. Observability endpoints

- `GET /health` — liveness + DB connectivity check.
- `GET /metrics` — Prometheus exposition format (Production Hardening
  Sprint H6), present whenever the `observability` extra is installed;
  absent (404) otherwise, never a hard dependency.
- Structured JSON logs to stdout — see `logging_config.py`; ship these via
  whatever your platform provides for stdout capture (CloudWatch Logs,
  Cloud Logging, a Fluent Bit sidecar, etc.) — nothing in this codebase
  ships logs anywhere itself.
- `SENTRY_DSN` (optional) — if set, unhandled exceptions are also reported
  to Sentry; unset by default, and the app behaves identically either way.

See `docs/OBSERVABILITY.md` for the full metrics/alerting/logging strategy.
