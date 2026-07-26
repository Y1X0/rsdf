"""Redis-backed fixed-window rate limiter (Production Hardening Sprint H4)
— closes the production readiness review's S2/SC1 finding: the in-process
`FixedWindowRateLimiter`'s state is per-process, so under N worker
processes the *effective* limit silently becomes
`configured_limit * N`. This class uses a single shared Redis key
(`INCR` + `EXPIRE`) so every worker/replica enforces the same window
against the same counter — the standard, well-known pattern for a
distributed fixed-window limiter.

`redis` is only imported here, lazily (inside `__init__`), matching every
other optional integration in this codebase — install with
`pip install '.[redis]'`. Never imported at module load time, so this
module can be imported (e.g. by the factory that decides whether to use
it) even when the `redis` package isn't installed.
"""

DEFAULT_KEY = "content_factory:rate_limit:auth_token"


class RedisFixedWindowRateLimiter:
    def __init__(self, *, redis_url: str, max_attempts: int, window_seconds: int, key: str = DEFAULT_KEY) -> None:
        import redis

        self._client = redis.Redis.from_url(redis_url)
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._key = key

    def allow(self) -> bool:
        """Same contract as FixedWindowRateLimiter.allow(): returns True and
        records the attempt if under the limit, False (but *does* still
        record — INCR already happened atomically) if the window is full.

        INCR is atomic, so concurrent callers across any number of
        processes/replicas always see a unique, correctly-ordered count —
        there is no race window here the way there is in the budget
        governor's check-then-act (see services/budget_governor.py's
        Postgres advisory-lock fix, added in this same sprint, for that
        different case)."""
        count = self._client.incr(self._key)
        if count == 1:
            # Only the request that just created the counter sets its
            # expiry — every subsequent INCR before expiry just increments
            # the existing key, exactly the fixed-window semantics
            # FixedWindowRateLimiter implements with a deque, translated to
            # a single Redis key.
            self._client.expire(self._key, self._window_seconds)
        return count <= self._max_attempts
