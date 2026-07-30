"""Production Hardening Sprint H4: the rate limiter factory's fallback
behavior — same "safe default + real backend" contract as every other
provider factory in this codebase."""

from content_factory.auth.rate_limiter import FixedWindowRateLimiter
from content_factory.auth.rate_limiter_factory import get_auth_rate_limiter
from content_factory.config import Settings


def test_memory_backend_returns_in_process_limiter():
    settings = Settings(rate_limit_backend="memory")
    limiter = get_auth_rate_limiter(settings)
    assert isinstance(limiter, FixedWindowRateLimiter)


def test_redis_backend_without_url_falls_back_to_memory():
    settings = Settings(rate_limit_backend="redis", redis_url="")
    limiter = get_auth_rate_limiter(settings)
    assert isinstance(limiter, FixedWindowRateLimiter)


def test_redis_backend_with_url_returns_redis_limiter():
    from content_factory.auth.redis_rate_limiter import RedisFixedWindowRateLimiter

    settings = Settings(rate_limit_backend="redis", redis_url="redis://localhost:6379/0")
    limiter = get_auth_rate_limiter(settings)
    assert isinstance(limiter, RedisFixedWindowRateLimiter)


def test_unknown_backend_falls_back_to_memory():
    settings = Settings(rate_limit_backend="something-invalid")
    limiter = get_auth_rate_limiter(settings)
    assert isinstance(limiter, FixedWindowRateLimiter)


def test_factory_uses_configured_thresholds():
    settings = Settings(
        rate_limit_backend="memory", auth_token_rate_limit_max_attempts=2, auth_token_rate_limit_window_seconds=99
    )
    limiter = get_auth_rate_limiter(settings)
    assert limiter._max_attempts == 2
    assert limiter._window_seconds == 99
