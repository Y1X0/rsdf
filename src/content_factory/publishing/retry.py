"""Retry-with-backoff for the first real external HTTP integrations this
codebase makes (PHASE1_AUDIT_v2.md F19 — "no retry/backoff around external
provider calls"). Bounded and simple: a fixed number of attempts,
exponential backoff, retrying only on the transient failure signal a
provider explicitly flags as worth retrying (5xx, timeout) — a 4xx (bad
request, invalid credentials) retrying wouldn't help and would just waste
API quota, so providers must raise something else for those.
"""

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE_SECONDS = 1.0


class RetryableProviderError(Exception):
    """Raised by a provider implementation to signal a transient failure
    (5xx, timeout) worth retrying — as opposed to a 4xx/auth error, which
    should propagate immediately without spending retry attempts on it."""


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    last_error: RetryableProviderError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except RetryableProviderError as exc:
            last_error = exc
            if attempt == max_attempts:
                raise
            sleep(backoff_base_seconds * (2 ** (attempt - 1)))
    raise last_error  # pragma: no cover - unreachable, loop always returns or raises
