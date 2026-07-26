"""Fixed-window rate limiter (PHASE1_AUDIT_v2.md N1 — "no rate limiting on
POST /auth/token"). In-process only: a single counter shared across
requests handled by *this* process. This was an accepted, documented
simplification through Phase 1/2 (a single trusted process didn't need
more), but the production readiness review correctly flagged it as a real
gap the moment more than one worker process is run: the effective limit
becomes `configured_limit * worker_count`, silently.

Production Hardening Sprint H4 closes this with a real, swappable
alternative — see `redis_rate_limiter.py::RedisFixedWindowRateLimiter` and
`rate_limiter_factory.py::get_auth_rate_limiter`, which resolves to this
class by default and to the Redis-backed one when
`RATE_LIMIT_BACKEND=redis` is configured (required the moment
`WEB_CONCURRENCY`/replica count is raised above 1 — see
docs/DEPLOYMENT.md §6). This class itself is unchanged and remains the
correct choice for genuinely single-process deployments.
"""

import time
from collections import deque
from threading import Lock
from typing import Protocol


class RateLimiter(Protocol):
    """Structural type both FixedWindowRateLimiter and
    RedisFixedWindowRateLimiter satisfy — the only thing any caller
    (api/routers/auth.py) actually depends on."""

    def allow(self) -> bool: ...


class FixedWindowRateLimiter:
    def __init__(self, max_attempts: int, window_seconds: float) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = Lock()

    def allow(self) -> bool:
        """Returns True and records the attempt if under the limit; returns
        False (and does not record) if the window is already full."""
        now = time.monotonic()
        with self._lock:
            while self._timestamps and self._timestamps[0] <= now - self._window_seconds:
                self._timestamps.popleft()
            if len(self._timestamps) >= self._max_attempts:
                return False
            self._timestamps.append(now)
            return True
