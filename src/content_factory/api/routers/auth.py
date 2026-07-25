"""Token issuance (PHASE1_AUDIT.md F2). Deliberately outside `require_auth`
— this is how a client *gets* a token in the first place.

Rate-limited (PHASE1_AUDIT_v2.md N1) since this is the one endpoint an
unauthenticated caller can hit repeatedly to brute-force credentials —
every other route already requires a valid token to reach it at all."""

import hmac

from fastapi import APIRouter, Depends, HTTPException

from content_factory.api.deps import get_auth_rate_limiter
from content_factory.auth.jwt_service import create_access_token
from content_factory.auth.rate_limiter import FixedWindowRateLimiter
from content_factory.config import Settings, get_settings
from content_factory.schemas.auth import TokenRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
def issue_token(
    payload: TokenRequest,
    settings: Settings = Depends(get_settings),
    rate_limiter: FixedWindowRateLimiter = Depends(get_auth_rate_limiter),
) -> TokenResponse:
    if not rate_limiter.allow():
        raise HTTPException(status_code=429, detail="Too many token requests — try again later")

    if not settings.jwt_secret_key or not settings.auth_client_secret:
        raise HTTPException(status_code=500, detail="Authentication is not configured on this server")

    # hmac.compare_digest is constant-time regardless of where the strings
    # first differ (or differ in length) — the right primitive for
    # comparing attacker-suppliable credentials against a known value.
    client_id_ok = hmac.compare_digest(payload.client_id, settings.auth_client_id)
    client_secret_ok = hmac.compare_digest(payload.client_secret, settings.auth_client_secret)
    if not (client_id_ok and client_secret_ok):
        raise HTTPException(status_code=401, detail="Invalid client credentials")

    token = create_access_token(subject=payload.client_id, role="operator", settings=settings)
    return TokenResponse(access_token=token, expires_in=settings.jwt_expires_minutes * 60)
