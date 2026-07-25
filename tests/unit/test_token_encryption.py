"""Phase 2 M3: OAuth tokens must never be stored in plaintext."""

import pytest
from cryptography.fernet import Fernet

from content_factory.config import Settings
from content_factory.services import token_encryption
from content_factory.services.token_encryption import TokenEncryptionNotConfigured


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
