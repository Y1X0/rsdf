"""Fixed-window rate limiter (PHASE1_AUDIT_v2.md N1 — "no rate limiting on
POST /auth/token"). In-process only: a single counter shared across
requests handled by this process. This is a deliberate, documented
simplification, not a hidden gap — a multi-worker or multi-instance
deployment would need a shared store (e.g. Redis) for this to limit
attempts across all of them; Phase 1/2's actual deployment target (a
single trusted process) doesn't need that yet, and adding a Redis
dependency purely for this would be exactly the kind of premature
infrastructure this codebase has consistently avoided (see docs/PHASE1.md's
"modular monolith, not microservices" rationale).
"""

import time
from collections import deque
from threading import Lock


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
