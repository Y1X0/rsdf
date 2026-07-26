# Observability

Production Hardening Sprint H6 — closes the production readiness review's
OBS1 finding ("no metrics, no error tracking, a health check that only
ever looked at the database, no request correlation, no alerting/log
guidance"). Referenced from `docs/DEPLOYMENT.md`.

## 1. Structured application logs

Already in place since Phase 1 (`logging_config.py`, `structlog`): every
log line is a single JSON object on stdout — `event`, `level`, `timestamp`,
plus whatever key-value context the call site bound. This sprint adds one
thing to that context: **every log line emitted while handling an HTTP
request now carries a `request_id`** (see §3), so `grep request_id=<id>`
against the container's log stream reconstructs everything that request
did — across routers, services, and agents — in order.

Nothing else changes: every agent/service already logs a start event, a
result/decision event, and an error event with `exc_info=True` on any
exception (see `agents/base.py`'s `agent_run()` for the enforced pattern).

## 2. Deployment logs

There is no log-shipping code in this repository, deliberately: the
container logs to stdout only (`docker-entrypoint.sh` never redirects it
anywhere else), and every mainstream container log driver/aggregator
(CloudWatch Logs, GCP Cloud Logging, Loki, Datadog's agent, a plain `docker
logs`) already captures stdout and, because each line is valid JSON,
indexes the fields automatically with no custom parser needed. Point your
platform's log collector at the container's stdout stream; there is
nothing else to configure here. `docker-compose.yml`'s `app` service
works the same way — `docker compose logs -f app` shows the same JSON
stream.

## 3. Request-ID correlation

`api/main.py`'s `_request_id_correlation` middleware runs on every
request: it takes the inbound `X-Request-ID` header if the caller (a
gateway, a load balancer, another service) already set one, otherwise
generates a UUID. Either way, it's bound to `structlog`'s contextvars for
the duration of the request (so it appears on every log line from that
request without every call site having to pass it explicitly) and echoed
back as the `X-Request-ID` response header, so a caller can log it
alongside their own trace and correlate the two sides of the call.

## 4. Metrics

`GET /metrics` (Prometheus exposition format), via
`prometheus-fastapi-instrumentator`. On by default (`METRICS_ENABLED=true`)
but only actually mounted if the `observability` extra is installed
(`pip install '.[observability]'` — already part of the `production`
bundle, see `pyproject.toml`); otherwise it logs `metrics_unavailable`
once at startup and the app runs exactly as before. Gives you, per route:
request count, request duration histogram, and in-progress request gauge
— point your Prometheus (or any compatible scraper) at it.

**What to actually alert on** (§5 has the fuller list) from these metrics:
a sustained rise in the 5xx rate for any route, and p95/p99 latency
regressions on the cost-incurring routes specifically (research,
script-gen, render, publish) — those are the ones a slow external
provider call would show up on first.

## 5. Error tracking

Sentry (`sentry-sdk[fastapi]`), initialized in
`observability.py::configure_error_tracking` — **off until `SENTRY_DSN` is
set**, matching this codebase's zero-secrets-required default everywhere
else. When set, it captures unhandled exceptions (including the ones the
catch-all handler in `api/main.py` already logs structurally) with the
deployment's `ENVIRONMENT` tagged as the Sentry environment.
`SENTRY_TRACES_SAMPLE_RATE` defaults to `0.0` (errors only, no performance
tracing) — raise it only if you actually want trace sampling and have
budgeted for the added Sentry event volume.

This is deliberately **additive to, not a replacement for**, the
structured stdout logs in §1/§2: Sentry is for "someone should look at
this specific exception," the log stream is for "reconstruct exactly what
happened." Keep both.

## 6. Health checks

`GET /health` (unauthenticated, matching every other health-check
convention) now reports a `checks` object, one entry per dependency this
deployment actually has:

- `database`: always checked (a fresh, request-independent connection —
  see the endpoint's own comment for why it's isolated from the request-
  scoped session).
- `redis`: only checked when `RATE_LIMIT_BACKEND=redis` and `REDIS_URL`
  is set — an unconfigured Redis isn't a health problem, it's simply not
  part of this deployment.

`200 {"status": "ok", "checks": {...}}` when every configured dependency
is reachable; `503 {"status": "unhealthy", "checks": {...}}` the moment
any one of them isn't — use this as both your container orchestrator's
liveness/readiness probe and your alerting system's synthetic check
target (§5 below).

## 7. Alerting strategy (recommendation, not implemented infrastructure)

This repository does not ship an alerting pipeline (no Alertmanager
config, no PagerDuty integration) — that's genuinely infrastructure that
belongs to wherever this gets deployed, not application code. The
recommendation, in priority order:

1. **`GET /health` returning non-200`, polled every 30-60s** — the single
   highest-value alert. Page immediately; this means the database (or, if
   configured, Redis) is unreachable.
2. **Sentry issue volume/rate spike** (§5) — page on a new error type or a
   sudden volume increase, not on every single event (that's what the log
   stream is for).
3. **Budget governor blocks** (`BudgetExceeded`, HTTP 402) — this is a
   *working-as-designed* fail-closed guardrail, not an incident, but a
   sustained pattern of them means a ceiling needs a human decision
   (ARCHITECTURE.md §7.3) — a daily digest/notification is more
   appropriate than a page. `NotificationProvider` (Slack/email, Phase 2
   M1) already fires on every 50/80/95/100% threshold crossing
   independent of this metrics/alerting layer — route that to a
   low-urgency channel, not the pager.
4. **5xx rate and cost-route latency** (§4) — a Prometheus alerting rule
   (`rate(http_requests_total{status=~"5.."}[5m]) > threshold`) is the
   standard shape; the exact threshold depends on real traffic volume this
   deployment doesn't have yet, so pick a starting point and tune it once
   there's production traffic to tune against rather than guessing a
   number now.
5. **CI failures on the default branch** (H2's GitHub Actions workflow) —
   already visible in GitHub's own UI/notifications; no extra wiring
   needed unless you want them mirrored into the same paging channel as
   the above.

Deliberately **not** recommended as page-worthy: individual `IdempotencyConflict`/
`IdempotencyInProgress` (409s) — these are expected client-retry
conditions, not incidents; and quality-gate auto-rejections — informational
by design (ARCHITECTURE.md §16), not a production health signal.
