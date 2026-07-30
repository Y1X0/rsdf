"""Phase 2 M3 — OAuth tokens (owned_accounts.encrypted_oauth_token) must
never be stored or logged in plaintext. Fernet (symmetric, authenticated
encryption) is deliberately promoted to a hard dependency rather than an
optional provider-style extra: token confidentiality is a security
requirement of this feature, not an optional integration surface like the
platform providers that sit alongside it.
"""

from cryptography.fernet import Fernet, InvalidToken

from content_factory.config import Settings
from content_factory.db.models.account import OwnedAccount
from content_factory.logging_config import get_logger

logger = get_logger(__name__)


class TokenEncryptionError(Exception):
    """Base for both ways token encryption can be unusable, so callers can
    catch one type and still get a real, actionable message either way."""


class TokenEncryptionNotConfigured(TokenEncryptionError):
    def __init__(self) -> None:
        super().__init__(
            "TOKEN_ENCRYPTION_KEY is not configured — cannot encrypt/decrypt OAuth tokens. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"'
        )


class TokenEncryptionMisconfigured(TokenEncryptionError):
    """Real bug this closes: TOKEN_ENCRYPTION_KEY was set to something that
    isn't a valid Fernet key (e.g. typed/pasted by hand instead of generated
    via Fernet.generate_key()). Fernet(...) raises a bare ValueError for
    this, which previously wasn't caught anywhere and fell through to the
    generic catch-all 500 in api/main.py — surfacing as an opaque
    "Internal server error" with zero indication of which env var, or what
    was wrong with it, actually caused the failure."""

    def __init__(self, exc: Exception) -> None:
        super().__init__(
            "TOKEN_ENCRYPTION_KEY is set but is not a valid Fernet key (must be 32 "
            "url-safe base64-encoded bytes, as produced by Fernet.generate_key() — not "
            "an arbitrary string). Generate one with: python -c \"from cryptography.fernet "
            f'import Fernet; print(Fernet.generate_key().decode())" — underlying error: {exc}'
        )


def _build_fernet(settings: Settings) -> Fernet:
    if not settings.token_encryption_key:
        raise TokenEncryptionNotConfigured()
    try:
        return Fernet(settings.token_encryption_key.encode())
    except ValueError as exc:
        raise TokenEncryptionMisconfigured(exc) from exc


def encrypt_token(raw_token: str, settings: Settings) -> str:
    return _build_fernet(settings).encrypt(raw_token.encode()).decode()


def decrypt_token(encrypted_token: str, settings: Settings) -> str:
    fernet = _build_fernet(settings)
    try:
        return fernet.decrypt(encrypted_token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("OAuth token could not be decrypted — wrong key or corrupted value") from exc


def resolve_access_token_or_none(account: OwnedAccount, settings: Settings) -> str | None:
    """Non-raising variant of decrypt_token, for automatic/best-effort call
    sites (the auto-publish and auto-metrics-sync cascades) that must
    degrade to "no token" rather than blow up a request that already
    succeeded on its own terms (a review decision, a publish that already
    went through). The explicit API endpoints (publications.py) still call
    decrypt_token directly and surface a real 500 - a human-triggered
    action with a genuinely misconfigured key should fail loudly, not
    silently degrade."""
    if not account.encrypted_oauth_token:
        return None
    try:
        return decrypt_token(account.encrypted_oauth_token, settings)
    except (ValueError, TokenEncryptionError):
        logger.warning("access_token_resolution_failed", account_id=account.id)
        return None
