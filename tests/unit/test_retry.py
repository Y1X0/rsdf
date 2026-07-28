"""Retry-with-backoff (PHASE1_AUDIT_v2.md F19), shared by every real
external HTTP provider (publishing, analytics ingestion, LLM,
transcription, TTS)."""

import pytest

from content_factory.retry import RetryableProviderError, call_with_retry


def test_call_with_retry_returns_immediately_on_success():
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        return "ok"

    result = call_with_retry(fn, sleep=lambda _: None)
    assert result == "ok"
    assert calls["count"] == 1


def test_call_with_retry_retries_then_succeeds():
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        if calls["count"] < 3:
            raise RetryableProviderError("transient")
        return "ok"

    sleeps = []
    result = call_with_retry(fn, max_attempts=3, backoff_base_seconds=1.0, sleep=sleeps.append)
    assert result == "ok"
    assert calls["count"] == 3
    assert sleeps == [1.0, 2.0]  # exponential backoff between attempts 1->2 and 2->3


def test_call_with_retry_exhausts_after_max_attempts():
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        raise RetryableProviderError("still failing")

    with pytest.raises(RetryableProviderError):
        call_with_retry(fn, max_attempts=3, sleep=lambda _: None)
    assert calls["count"] == 3


def test_call_with_retry_does_not_retry_non_retryable_errors():
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        raise ValueError("not retryable")

    with pytest.raises(ValueError):
        call_with_retry(fn, sleep=lambda _: None)
    assert calls["count"] == 1
