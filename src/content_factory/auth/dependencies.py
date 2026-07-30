"""FastAPI dependencies enforcing bearer-token authentication
(`require_auth`) and role-gated authorization (`require_operator`) —
applied explicitly to every business route (see api/routers/*.py), the
same way `Depends(get_db)` is. Only `/health` and `POST /auth/token`
are exempt.
"""

from fastapi import Depends, Header, HTTPException, status

from content_factory.auth.jwt_service import TokenError, decode_access_token
from content_factory.config import Settings, get_settings


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def require_auth(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Baseline for every business route, reads and writes alike: a valid,
    non-expired token is required. Fails closed (500, not 401) if the
    server itself has no JWT_SECRET_KEY configured — an unconfigured
    server should refuse to authenticate anyone, not silently accept
    unverifiable tokens or, worse, allow unauthenticated access."""
    if not settings.jwt_secret_key:
        raise HTTPException(status_code=500, detail="Authentication is not configured on this server")
    token = _extract_bearer_token(authorization)
    try:
        return decode_access_token(token, settings)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_operator(payload: dict = Depends(require_auth)) -> dict:
    """Authorization on top of authentication: mutating endpoints (anything
    that spends money, changes state, or records a review decision) require
    the 'operator' role, not merely a valid token. Phase 1 issues only
    'operator' tokens today; this is the seam a future read-only role
    plugs into without touching every route that calls it."""
    if payload.get("role") != "operator":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator role required")
    return payload
