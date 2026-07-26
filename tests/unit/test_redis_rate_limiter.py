"""Production Hardening Sprint H4 (S2/SC1): the Redis-backed rate limiter
must enforce a single shared window regardless of how many separate
`RedisFixedWindowRateLimiter` instances point at it — that's the whole
point (simulating N worker processes each with their own Python object,
all still sharing state through Redis). Requires a real local Redis
instance (this sandbox has one; skips cleanly if unavailable so the suite
still runs in an environment without Redis installed/running)."""

import uuid

import pytest

redis = pytest.importorskip("redis")

from content_factory.auth.redis_rate_limiter import RedisFixedWindowRateLimiter  # noqa: E402

REDIS_URL = "redis://localhost:6379/0"


def _redis_available() -> bool:
    try:
        redis.Redis.from_url(REDIS_URL).ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _redis_available(), reason="no local Redis instance available")


def _unique_key() -> str:
    return f"test:rate_limit:{uuid.uuid4()}"


def test_allows_up_to_max_attempts():
    limiter = RedisFixedWindowRateLimiter(
        redis_url=REDIS_URL, max_attempts=3, window_seconds=60, key=_unique_key()
    )
    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is False


def test_state_is_shared_across_separate_instances_same_key():
    """The regression this class exists for: two independent Python
    objects (standing in for two separate worker processes) must share
    one counter through Redis, unlike FixedWindowRateLimiter's in-process
    deque, which would give each its own independent count of 3."""
    key = _unique_key()
    limiter_a = RedisFixedWindowRateLimiter(redis_url=REDIS_URL, max_attempts=3, window_seconds=60, key=key)
    limiter_b = RedisFixedWindowRateLimiter(redis_url=REDIS_URL, max_attempts=3, window_seconds=60, key=key)

    assert limiter_a.allow() is True   # count=1
    assert limiter_b.allow() is True   # count=2 (shared!)
    assert limiter_a.allow() is True   # count=3
    assert limiter_b.allow() is False  # count=4, over the shared limit of 3


def test_different_keys_are_independent():
    limiter_a = RedisFixedWindowRateLimiter(redis_url=REDIS_URL, max_attempts=1, window_seconds=60, key=_unique_key())
    limiter_b = RedisFixedWindowRateLimiter(redis_url=REDIS_URL, max_attempts=1, window_seconds=60, key=_unique_key())

    assert limiter_a.allow() is True
    assert limiter_a.allow() is False
    assert limiter_b.allow() is True  # unaffected by limiter_a's separate key


def test_window_expiry_is_set_only_on_first_increment():
    key = _unique_key()
    limiter = RedisFixedWindowRateLimiter(redis_url=REDIS_URL, max_attempts=5, window_seconds=30, key=key)
    client = redis.Redis.from_url(REDIS_URL)

    limiter.allow()
    ttl_after_first = client.ttl(key)
    assert 0 < ttl_after_first <= 30

    limiter.allow()
    ttl_after_second = client.ttl(key)
    # Still counting down from the same original window, not reset.
    assert 0 < ttl_after_second <= ttl_after_first
