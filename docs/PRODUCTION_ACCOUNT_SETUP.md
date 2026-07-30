# Production Account Setup (one-time admin procedure)

This is a **manual, one-time (or rare — once per new platform account)
admin action**, not a CI/CD step. Registering a publishing account is a
real operational decision (which real audience this pipeline posts to)
and involves a real, sensitive credential (a platform access token) — it
must never be baked into an automated workflow that runs repeatedly,
for exactly the reasons already avoided elsewhere in this project's
CI (`.github/workflows/verify-production.yml` runs on `workflow_dispatch`
only, never on push/PR):

- A workflow that re-runs `POST /accounts` on every trigger risks
  creating duplicate accounts or silently re-registering a stale one.
- OAuth/access-token lifecycle (rotation, expiry, revocation) belongs to
  a human decision, not an automated pipeline step.

Do this once, from a terminal with real network access to the live
deployment (a local machine, a Codespace, etc.) — never paste secrets
into a chat/AI session; only into the terminal itself.

## 1. Prerequisites

- A real Meta Developer App (the one whose ID/secret are already set as
  `INSTAGRAM_APP_ID`/`INSTAGRAM_APP_SECRET` in Render's Environment tab —
  see `render.yaml`'s own comment on why this two-key gate exists in
  `publishing/factory.py`).
- A real, valid access token issued via **Instagram API with Instagram
  Login** (token prefixed `IGAA...`) — this is the flow
  `publishing/providers/instagram_provider.py` targets
  (`graph.instagram.com`). A token from the other Meta product,
  "Facebook Login for Business" (Page-linked, `graph.facebook.com`), is a
  different format and will fail with Meta's `"Invalid OAuth access
  token - Cannot parse access token"` (code 190) against this provider —
  a real failure mode this exact document's first live run hit.
- The real numeric Instagram User ID (`platform_account_id` in the API
  below) — **not** the handle; Instagram's Graph API is node-based and
  does not resolve `"me"` to your account (see
  `publishing/providers/instagram_provider.py`).
- A real `AUTH_CLIENT_SECRET` (from Render's Environment tab).

## 2. Obtain an auth token

```bash
read -rs -p "Paste AUTH_CLIENT_SECRET, then Enter: " AUTH_CLIENT_SECRET
echo
TOKEN=$(curl -sS -X POST https://content-factory-bhhd.onrender.com/auth/token \
  -H "content-type: application/json" \
  --data "$(python3 -c 'import json,os; print(json.dumps({"client_id":"pilot-operator","client_secret":os.environ["AUTH_CLIENT_SECRET"]}))')" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
```

## 3. Register the account

```bash
read -rs -p "Paste the real Instagram access token, then Enter: " IG_TOKEN
echo
curl -sS -X POST https://content-factory-bhhd.onrender.com/accounts \
  -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
  --data "$(python3 -c 'import json,os; print(json.dumps({
    "platform": "instagram",
    "handle": "@your_real_ig_handle",
    "platform_account_id": "THE_REAL_NUMERIC_IG_BUSINESS_ACCOUNT_ID",
    "oauth_token": os.environ["IG_TOKEN"],
  }))')"
```

A freshly-created account defaults to `status=active` and
`health_tier=healthy` (`db/models/account.py`) — no separate
"activation" step is needed for it to become eligible for auto-publish.

**If you registered an account earlier via a mobile terminal and it may
have failed partway (e.g. an auth error before the request was ever
sent)**: re-running this is safe. A second attempt with the *same*
`platform`+`handle` returns `409 Conflict` (`{"detail": "Account
instagram:'@your_real_ig_handle' already exists"}`) rather than creating
a duplicate — this is enforced by a database-level unique constraint on
`(platform, handle)`, not just an application-level check, and is
covered by `tests/api/test_accounts_api.py::test_duplicate_platform_handle_rejected`.
A `409` here means the account you're trying to register is *already*
present — check step 4 below rather than assuming registration never
worked.

## 4. Verify

```bash
curl -sS https://content-factory-bhhd.onrender.com/accounts -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Confirm the account shows `"status": "active"`, `"health_tier":
"healthy"`, and `"has_credentials": true` (the token itself is never
returned, only this boolean). This is exactly what
`services/publishing_service.py::_select_auto_publish_account` requires
to select it automatically — see
`tests/unit/test_publishing_service.py::test_attempt_auto_publish_succeeds_with_exactly_one_eligible_account`
for the exact, already-passing regression test proving a bare account
created this way is selected and carried through the full auto-publish
cascade.

## 5. Re-run the production verification

Either `scripts/verify_production_pipeline.sh` from a terminal, or the
`Verify production pipeline` GitHub Actions workflow
(`workflow_dispatch`, no code change needed). The only line that should
change in the final summary is:

```
FAIL - auto-publish was skipped: no eligible ... account is registered
```

becoming:

```
PASS - auto-publish reached a real provider ...
```

or

```
PASS - auto-publish cascade ran with zero manual calls ... : scheduled
```

(`"scheduled"` is the correct, honest outcome via `ManualPublishingProvider`
if `INSTAGRAM_APP_ID`/`APP_SECRET` aren't both set for real yet — see
`docs/DEPLOYMENT.md` §8b; `"published"` means it reached the real
Instagram provider.)
