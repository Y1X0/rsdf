# Production Deployment Verification

**Purpose:** the exact steps to deploy this system to a real server and verify
it for real — specifically closing the one gap this development sandbox
cannot close itself: a real LLM provider call. This sandbox's own outbound
egress policy allowlists `anthropic.com` but not `api.groq.com` (documented
in `docs/PILOT_ENVIRONMENT_STATUS.md` row 1), so Groq has only ever been
verified with mocked HTTP responses (`tests/unit/test_groq_provider.py`),
never a live call. **That is a property of this sandbox, not of the code or
the Groq integration, and is not re-investigated here** — this document is
written for a normal server environment, which has no such restriction.

**Scope discipline:** this is a deployment and verification document only.
No application code, schema, or architecture changes are made or proposed
here — every command below exercises something that already exists
(`docs/DEPLOYMENT.md`'s topology, `docs/PHASE1.md`'s API surface, the Groq
integration already committed and unit-tested).

---

## 1. Production deployment checklist

Ordered; each step assumes the previous one succeeded. This consolidates
`docs/DEPLOYMENT.md`'s full explanation into an actionable, checkable
sequence for the first real deploy.

- [ ] **1.1 Provision a real Postgres 16 instance** (managed service or
      self-hosted) — not SQLite, not this sandbox's local instance. Record
      the connection string as `DATABASE_URL`.
- [ ] **1.2 Provision Redis** if `WEB_CONCURRENCY` will be raised above `1`
      (`docs/DEPLOYMENT.md` §6 — the in-process rate limiter is unsafe
      across multiple workers without it). Record `REDIS_URL`.
- [ ] **1.3 Generate real secrets** (run these on the target server or a
      secure secret-management workstation, never reuse a value from
      `tests/conftest.py` or this sandbox's own `.env`):
      ```bash
      python -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET_KEY
      python -c "import secrets; print(secrets.token_urlsafe(32))"   # AUTH_CLIENT_SECRET
      python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # TOKEN_ENCRYPTION_KEY
      ```
- [ ] **1.4 Set every mandatory environment variable** on the real server
      (real secret manager / platform env config — never a `.env` file
      shipped inside the image or repo). See §2 below for the full list.
- [ ] **1.5 Set `ENVIRONMENT=production`** — this activates
      `Settings.validate_production_safety()` (`docs/DEPLOYMENT.md` §2),
      which fails closed at startup if `DATABASE_URL` is SQLite or
      `JWT_SECRET_KEY` is missing/weak. A refusal to start here is the
      check working correctly, not a bug.
- [ ] **1.6 Build the image** on the real server or its CI (§3 verifies
      this build is structurally correct; it has not been executed in this
      sandbox — see §3's note on why).
      ```bash
      docker build -t content-factory:latest .
      ```
- [ ] **1.7 Run migrations to completion, once, before any `serve` replica
      starts** (`docker-entrypoint.sh`'s explicit `migrate`/`serve` dispatch
      exists precisely so this never happens implicitly or racily):
      ```bash
      docker run --rm --env-file .env.production content-factory:latest migrate
      ```
- [ ] **1.8 Start the app**:
      ```bash
      docker run -d --env-file .env.production -p 8000:8000 content-factory:latest serve
      ```
- [ ] **1.9 Verify `/health` reports every configured dependency `ok`**:
      ```bash
      curl -sS http://<real-host>:8000/health | python3 -m json.tool
      ```
      Expect `200` with `"status": "ok"` and a `"database"` (and `"redis"`,
      if configured) check both `ok` — `503` means a real dependency is
      unreachable; do not proceed past a `503`.
- [ ] **1.10 Set a real budget ceiling** sized for this first verification
      run (a small, specific dollar amount, not the full pilot budget):
      ```bash
      curl -sS -X POST http://<real-host>:8000/budget/ceilings \
        -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
        -d '{"scope": "system", "monthly_limit_usd": 5.00}'
      ```
- [ ] **1.11 Confirm the rollback lever works**: flip `PUBLISHING_ENABLED=false`,
      restart, confirm `POST /videos/{id}/publish` returns `503`, flip back
      to `true`, restart again.
- [ ] **1.12 Only after 1.1-1.11 pass**, proceed to §4 (the first real
      Research Agent call).

---

## 2. Required environment variables

Source of truth is `.env.example` (kept in exact sync with
`content_factory.config.Settings` — verified field-by-field in
`docs/FINAL_PROJECT_STATUS.md`). Grouped here by whether a real first
deployment can start without them.

### Mandatory for any real deployment

| Variable | Purpose | Notes |
|---|---|---|
| `DATABASE_URL` | Real Postgres connection | Must not be SQLite once `ENVIRONMENT=production` (fails closed otherwise) |
| `ENVIRONMENT` | Set to `production` | Activates the fail-closed startup safety check |
| `JWT_SECRET_KEY` | Auth token signing | ≥32 chars, generate fresh — see §1.3 |
| `AUTH_CLIENT_ID` / `AUTH_CLIENT_SECRET` | Operator credentials for `POST /auth/token` | Generate a fresh secret, don't reuse any test value |
| **One LLM provider's credentials** (see below) | Research/Script Agent | At least one of Anthropic or Groq must be configured for real content generation |

**LLM provider — exactly one of these two blocks, selected via `LLM_PROVIDER`:**

| Variable | Required when |
|---|---|
| `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_MODEL_VERSION` | Using Anthropic (needs billing/credits on the account — see `docs/PILOT_ENVIRONMENT_STATUS.md` row 1) |
| `LLM_PROVIDER=groq`, `GROQ_API_KEY`, `GROQ_MODEL` (default `llama-3.3-70b-versatile`) | Using Groq (free-tier; not yet live-verified anywhere — see §4) |

If the selected provider's key is empty, `resolved_llm_provider()` silently
falls back to `"fake"` — the app still runs, but produces no real content.
**Confirm the key is actually non-empty in the real environment before
relying on any result from §4.**

### Mandatory only once you exercise the corresponding feature

| Variable | Needed for |
|---|---|
| `TOKEN_ENCRYPTION_KEY` | Registering any creator account with a real OAuth token (`POST /accounts`) — fails closed (500) without it |
| `RATE_LIMIT_BACKEND=redis` + `REDIS_URL` | Running more than one `uvicorn` worker (`WEB_CONCURRENCY > 1`) |
| `RENDERER_BACKEND=template_pillow` | Producing a real playable video file instead of a render manifest (needs the `rendering` extra installed) |

### Optional — safe defaults exist for all of these

| Variable | Default behavior without it |
|---|---|
| `TTS_PROVIDER` / `ELEVENLABS_API_KEY` | `silent` — placeholder audio manifest, no external call |
| `TIKTOK_*` / `YOUTUBE_*` / `INSTAGRAM_*` | Falls back to `ManualPublishingProvider` per platform |
| `NOTIFICATION_PROVIDER` / `SLACK_WEBHOOK_URL` / `SMTP_*` | `log` — structured log line, no external call |
| `MEDIA_BACKUP_ENABLED` / `MEDIA_BACKUP_S3_*` | Off — local disk only |
| `METRICS_ENABLED` / `SENTRY_DSN` | Metrics on (needs `observability` extra or no-ops); Sentry off |
| `QUALITY_ORIGINALITY_AUTO_REJECT_FLOOR` / `QUALITY_POLICY_RISK_AUTO_REJECT_CEILING` | `0` / `100` — auto-reject gate disabled |
| `PUBLISHING_ENABLED` | `true` — set `false` as the emergency kill-switch (§1.11) |

---

## 3. Docker deployment instructions — verified

Reviewed `Dockerfile`, `docker-compose.yml`, and `docker-entrypoint.sh` for
internal consistency and for compatibility with the Groq integration added
since these files were last touched. Findings:

- **Multi-stage build is correct and unchanged by Groq.** The builder stage
  installs from `requirements-lock.txt` into an isolated venv; the runtime
  stage copies it verbatim, runs as a non-root user, and declares a real
  `HEALTHCHECK` against `/health`.
- **No image rebuild is needed to support Groq.** `requirements-lock.txt`
  already pins `httpx==0.28.1` — a transitive dependency of the `anthropic`
  SDK, which is a core (non-optional) dependency — so `GroqLLMClient`'s
  lazy `import httpx` already succeeds in the existing image. The `groq`
  extra in `pyproject.toml` (`httpx>=0.27`) is redundant with what's already
  installed; it exists for clarity and for anyone installing outside the
  locked image (e.g. `pip install '.[groq]'` in a plain venv), not because
  the Docker image is missing anything.
- **Migrate/serve dispatch is correct.** `docker-entrypoint.sh` only runs
  `alembic upgrade head` when explicitly invoked as `migrate`; `serve` (the
  default `CMD`) never touches migrations — matching `docs/DEPLOYMENT.md`
  §5's documented sequence exactly.
- **`docker-compose.yml`'s topology matches production's shape**: real
  Postgres 16 + Redis 7 containers, a one-off `migrate` profile service,
  and the `app` service pre-configured for `RATE_LIMIT_BACKEND=redis` at
  `WEB_CONCURRENCY: "2"` as the reference multi-worker example.
- **This was a structural review, not an executed build.** `docker build`
  was not run in this sandbox: pulling `python:3.11-slim` from Docker Hub is
  blocked by this session's own egress policy (the same class of restriction
  documented for Groq — see `docs/FINAL_PROJECT_STATUS.md`'s CI findings for
  the earlier, independent confirmation of this via a transient CI registry
  timeout). Per the current instruction, this is not re-investigated here.
  **Run the actual build verification on the real server**:
  ```bash
  docker build -t content-factory:verify .
  docker run --rm content-factory:verify python -c "import httpx, anthropic; print('deps ok')"
  docker compose build
  docker compose run --rm migrate
  docker compose up -d app
  curl -sS http://localhost:8000/health
  ```
  All five commands are expected to succeed with no changes to any
  Docker-related file — if any fails, that is new information about the
  target server's environment, not a known/expected gap.

---

## 4. Commands: first real Research Agent test, from a normal server

Run these from the real deployed server (or anywhere with normal, unrestricted
outbound internet access — **not** this sandbox), against the real deployment
from §1. This works for whichever provider `LLM_PROVIDER` selects — Anthropic
or Groq — the request/response shape is identical either way.

```bash
# 0. Confirm which provider is actually active and that its key is non-empty
#    (never print the key itself)
curl -sS http://<real-host>:8000/health | python3 -m json.tool

# 1. Get a real operator token
TOKEN=$(curl -sS -X POST http://<real-host>:8000/auth/token \
  -H "Content-Type: application/json" \
  -d "{\"client_id\": \"$AUTH_CLIENT_ID\", \"client_secret\": \"$AUTH_CLIENT_SECRET\"}" \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

# 2. Create one throwaway, clearly-named campaign (niche is auto-created by name)
CAMPAIGN_ID=$(curl -sS -X POST http://<real-host>:8000/campaigns \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"brand_name": "_deployment_verification_delete_me", "niche_name": "_deployment_verification_niche"}' \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "campaign_id=$CAMPAIGN_ID"

# 3. Run the real Research Agent call
curl -sS -X POST "http://<real-host>:8000/campaigns/$CAMPAIGN_ID/research" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"raw_notes": "Verification-only call: confirm this reaches a real LLM provider and returns genuine, non-canned content."}' \
  | python3 -m json.tool
```

**Pass criterion:** the response's `brief_text` and `structured_data` contain
genuine, specific, non-empty generated text — not an empty/canned shape.
If it looks canned or empty, the configured provider's key isn't actually
being used; re-check `resolved_llm_provider()`'s fallback condition (an
empty key silently falls back to `"fake"`) before concluding anything else
is wrong.

**Confirm which provider actually served the request** by checking the
server's structured logs for the request's `agent_run_started`/
`agent_run_completed` log lines (or, if instrumented, the `provider` field
on the underlying `LLMResponse`) — don't infer it only from `LLM_PROVIDER`
being set, since a misconfigured key falls back silently rather than erroring.

```bash
# 4. Clean up the throwaway campaign/niche once the result is recorded —
#    do not let verification data contaminate real pilot data (see the
#    exact FK-safe deletion order already used for prior verifications:
#    agent_run -> research_brief -> campaign -> niche).
```

Once this passes, record the evidence (response body excerpt, timestamp,
which provider served it) in `docs/PILOT_ENVIRONMENT_STATUS.md` row 1 and
flip that row to `READY`.

---

## 5. What this document does not do

No application code, migration, schema, Dockerfile, or compose file was
modified to produce this document — every command above targets something
already built and already tested (mocked, for Groq's real-HTTP path). The
one remaining gap this document cannot close itself is executing §3's and
§4's commands against a real, unrestricted server — that step requires an
actual deployment target, which is outside what any sandboxed development
session can provide.
