"""JWT issuance/verification (PHASE1_AUDIT.md F2 — "no authentication or
authorization on any endpoint").

Phase 1 has no user database, and inventing one would be scope creep beyond
what a stability/security patch release should add — it has exactly one
real-world operator today. So tokens are issued to a small, fixed set of
pre-shared "client" identities configured via environment variables
(`AUTH_CLIENT_ID`/`AUTH_CLIENT_SECRET`), which is the standard client-
credentials pattern for service-to-service auth, not a home-grown scheme.
A real per-user store is Phase 2+ territory once there's more than one
operator to distinguish.
"""

from datetime import UTC, datetime, timedelta

import jwt

from content_factory.config import Settings

# Keep in sync with Settings.jwt_algorithm. Restricting to a single,
# explicitly-allowed algorithm (rather than trusting whatever the token
# itself claims) avoids the classic JWT "alg confusion" class of bugs.
_ALLOWED_ALGORITHMS = ("HS256",)


class TokenError(Exception):
    """Any invalid, expired, or malformed token. Callers map this to a 401."""


def create_access_token(*, subject: str, role: str, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expires_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict:
    if settings.jwt_algorithm not in _ALLOWED_ALGORITHMS:
        raise TokenError(f"Unsupported JWT algorithm configured: {settings.jwt_algorithm!r}")
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
