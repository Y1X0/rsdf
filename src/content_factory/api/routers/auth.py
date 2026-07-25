"""Token issuance (PHASE1_AUDIT.md F2). Deliberately outside `require_auth`
— this is how a client *gets* a token in the first place."""

import hmac

from fastapi import APIRouter, Depends, HTTPException

from content_factory.auth.jwt_service import create_access_token
from content_factory.config import Settings, get_settings
from content_factory.schemas.auth import TokenRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
def issue_token(payload: TokenRequest, settings: Settings = Depends(get_settings)) -> TokenResponse:
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
