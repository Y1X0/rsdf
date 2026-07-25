"""Phase 2 M3 — OAuth tokens (owned_accounts.encrypted_oauth_token) must
never be stored or logged in plaintext. Fernet (symmetric, authenticated
encryption) is deliberately promoted to a hard dependency rather than an
optional provider-style extra: token confidentiality is a security
requirement of this feature, not an optional integration surface like the
platform providers that sit alongside it.
"""

from cryptography.fernet import Fernet, InvalidToken

from content_factory.config import Settings


class TokenEncryptionNotConfigured(Exception):
    def __init__(self) -> None:
        super().__init__(
            "TOKEN_ENCRYPTION_KEY is not configured — cannot encrypt/decrypt OAuth tokens. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"'
        )


def encrypt_token(raw_token: str, settings: Settings) -> str:
    if not settings.token_encryption_key:
        raise TokenEncryptionNotConfigured()
    return Fernet(settings.token_encryption_key.encode()).encrypt(raw_token.encode()).decode()


def decrypt_token(encrypted_token: str, settings: Settings) -> str:
    if not settings.token_encryption_key:
        raise TokenEncryptionNotConfigured()
    try:
        return Fernet(settings.token_encryption_key.encode()).decrypt(encrypted_token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("OAuth token could not be decrypted — wrong key or corrupted value") from exc
