"""Selects the auth rate limiter backend (Production Hardening Sprint H4),
same "safe default + real backend" shape as every other provider factory
in this codebase (llm/factory.py, notifications/factory.py, etc.): falls
back to the in-process limiter — with a logged warning — whenever the
Redis backend is requested but not actually usable (package not
installed, or no REDIS_URL configured), rather than crashing.
"""

from content_factory.auth.rate_limiter import FixedWindowRateLimiter, RateLimiter
from content_factory.config import Settings
from content_factory.logging_config import get_logger

logger = get_logger(__name__)


def get_auth_rate_limiter(settings: Settings) -> RateLimiter:
    if settings.rate_limit_backend == "redis":
        if not settings.redis_url:
            logger.warning("rate_limiter_fallback", reason="no_redis_url_configured")
        else:
            try:
                from content_factory.auth.redis_rate_limiter import RedisFixedWindowRateLimiter

                return RedisFixedWindowRateLimiter(
                    redis_url=settings.redis_url,
                    max_attempts=settings.auth_token_rate_limit_max_attempts,
                    window_seconds=settings.auth_token_rate_limit_window_seconds,
                )
            except ImportError:
                logger.warning("rate_limiter_fallback", reason="redis_package_not_installed")

    return FixedWindowRateLimiter(
        max_attempts=settings.auth_token_rate_limit_max_attempts,
        window_seconds=settings.auth_token_rate_limit_window_seconds,
    )
