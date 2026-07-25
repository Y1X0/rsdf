"""Production Hardening Sprint H1: `Settings.validate_production_safety`
must fail closed on the exact footguns the review flagged — silent SQLite
in production, and a missing/weak JWT secret — while staying a total
no-op for every non-production environment (i.e. every existing test)."""

import pytest

from content_factory.config import Settings


def test_non_production_environment_is_always_a_no_op():
    settings = Settings(environment="development", database_url="sqlite:///./x.db", jwt_secret_key="")
    settings.validate_production_safety()  # must not raise


def test_production_with_sqlite_raises():
    settings = Settings(
        environment="production",
        database_url="sqlite:///./x.db",
        jwt_secret_key="a" * 40,
    )
    with pytest.raises(RuntimeError, match="SQLite"):
        settings.validate_production_safety()


def test_production_with_missing_jwt_secret_raises():
    settings = Settings(
        environment="production",
        database_url="postgresql+psycopg2://u:p@host/db",
        jwt_secret_key="",
    )
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY is unset"):
        settings.validate_production_safety()


def test_production_with_short_jwt_secret_raises():
    settings = Settings(
        environment="production",
        database_url="postgresql+psycopg2://u:p@host/db",
        jwt_secret_key="too-short",
    )
    with pytest.raises(RuntimeError, match="32"):
        settings.validate_production_safety()


def test_production_with_real_postgres_and_strong_secret_passes():
    settings = Settings(
        environment="production",
        database_url="postgresql+psycopg2://u:p@host/db",
        jwt_secret_key="a" * 40,
    )
    settings.validate_production_safety()  # must not raise


def test_production_reports_every_problem_at_once():
    settings = Settings(environment="production", database_url="sqlite:///./x.db", jwt_secret_key="")
    with pytest.raises(RuntimeError) as exc_info:
        settings.validate_production_safety()
    assert "SQLite" in str(exc_info.value)
    assert "JWT_SECRET_KEY" in str(exc_info.value)
