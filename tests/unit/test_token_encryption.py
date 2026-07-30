"""Phase 2 M3: OAuth tokens must never be stored in plaintext."""

import pytest
from cryptography.fernet import Fernet

from content_factory.config import Settings
from content_factory.services import token_encryption
from content_factory.services.token_encryption import TokenEncryptionMisconfigured, TokenEncryptionNotConfigured


def _settings_with_key() -> Settings:
    return Settings(token_encryption_key=Fernet.generate_key().decode())


def test_encrypt_then_decrypt_round_trips():
    settings = _settings_with_key()
    encrypted = token_encryption.encrypt_token("real-oauth-token", settings)
    assert encrypted != "real-oauth-token"
    assert token_encryption.decrypt_token(encrypted, settings) == "real-oauth-token"


def test_encrypt_raises_when_key_not_configured():
    settings = Settings(token_encryption_key="")
    with pytest.raises(TokenEncryptionNotConfigured):
        token_encryption.encrypt_token("token", settings)


def test_decrypt_raises_value_error_on_corrupted_value():
    settings = _settings_with_key()
    with pytest.raises(ValueError):
        token_encryption.decrypt_token("not-a-valid-fernet-token", settings)


def test_decrypt_fails_with_a_different_key():
    settings_a = _settings_with_key()
    settings_b = _settings_with_key()
    encrypted = token_encryption.encrypt_token("secret", settings_a)
    with pytest.raises(ValueError):
        token_encryption.decrypt_token(encrypted, settings_b)


def test_encrypt_raises_a_clear_error_when_key_is_not_a_valid_fernet_key():
    """Real production bug this closes: a hand-typed/pasted
    TOKEN_ENCRYPTION_KEY that isn't a real Fernet.generate_key() value made
    Fernet(...) raise a bare, uncaught ValueError, which fell through to
    the API's generic catch-all and surfaced as an opaque "Internal server
    error" with no indication of which env var — or what was wrong with
    it — actually caused the failure."""
    settings = Settings(token_encryption_key="not-a-real-fernet-key")
    with pytest.raises(TokenEncryptionMisconfigured, match="TOKEN_ENCRYPTION_KEY"):
        token_encryption.encrypt_token("token", settings)


def test_decrypt_raises_a_clear_error_when_key_is_not_a_valid_fernet_key():
    settings = Settings(token_encryption_key="also-not-a-real-fernet-key")
    with pytest.raises(TokenEncryptionMisconfigured, match="TOKEN_ENCRYPTION_KEY"):
        token_encryption.decrypt_token("irrelevant", settings)
