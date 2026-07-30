"""ElevenLabsTTSProvider - tested against mocked HTTP responses only, never
a live network call, matching this codebase's zero-secrets-required test
philosophy (same pattern as test_groq_provider.py / test_groq_whisper_provider.py).

Focused specifically on the retry/error-handling behavior added alongside
PHASE1_AUDIT_v2.md's F19 pickup: this provider previously had zero retry
coverage and zero error-body wrapping (a bare httpx.HTTPStatusError would
propagate with no hint of *why*)."""

from pathlib import Path

import httpx
import pytest

from content_factory.video_production.tts.providers.elevenlabs_provider import ElevenLabsTTSProvider


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"", text: str = ""):
        self.status_code = status_code
        self.content = content
        self.text = text or content.decode(errors="ignore")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def test_synthesize_returns_a_real_audio_file_and_marks_provider_as_elevenlabs(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(200, content=b"fake-mp3-bytes"))
    provider = ElevenLabsTTSProvider(api_key="test-key", storage_dir=tmp_path)

    result = provider.synthesize(text="hello world", voice_id="voice-1")

    assert result.provider == "elevenlabs"
    assert result.cost_usd > 0
    assert Path(result.audio_path).exists()


def test_synthesize_raises_a_runtime_error_carrying_elevenlabs_actual_error_body_on_http_error_status(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(401, text='{"detail": "invalid api key"}'))
    provider = ElevenLabsTTSProvider(api_key="bad-key", storage_dir=tmp_path)

    with pytest.raises(RuntimeError) as exc_info:
        provider.synthesize(text="hello", voice_id="voice-1")
    assert "401" in str(exc_info.value)
    assert "invalid api key" in str(exc_info.value)


def test_synthesize_retries_on_5xx_then_succeeds(tmp_path, monkeypatch):
    """PHASE1_AUDIT_v2.md F19 (retry/backoff around external provider
    calls) had never been applied to this provider - a real narration
    synthesis call would fail outright on one transient ElevenLabs hiccup
    instead of quietly recovering, the same gap already closed for the
    publishing/analytics-ingestion/LLM/transcription providers."""
    calls = {"count": 0}

    def _fake_post(*a, **k):
        calls["count"] += 1
        if calls["count"] < 2:
            return _FakeResponse(503)
        return _FakeResponse(200, content=b"fake-mp3-bytes")

    monkeypatch.setattr(httpx, "post", _fake_post)
    provider = ElevenLabsTTSProvider(api_key="test-key", storage_dir=tmp_path)
    result = provider.synthesize(text="hello world", voice_id="voice-1")

    assert Path(result.audio_path).exists()
    assert calls["count"] == 2


def test_synthesize_retries_on_timeout_then_succeeds(tmp_path, monkeypatch):
    calls = {"count": 0}

    def _fake_post(*a, **k):
        calls["count"] += 1
        if calls["count"] < 2:
            raise httpx.TimeoutException("timed out")
        return _FakeResponse(200, content=b"fake-mp3-bytes")

    monkeypatch.setattr(httpx, "post", _fake_post)
    provider = ElevenLabsTTSProvider(api_key="test-key", storage_dir=tmp_path)
    result = provider.synthesize(text="hello world", voice_id="voice-1")

    assert Path(result.audio_path).exists()
    assert calls["count"] == 2


def test_synthesize_does_not_retry_on_4xx(tmp_path, monkeypatch):
    """A bad API key or invalid voice_id won't fix itself on retry -
    retrying would just waste attempts, so a 4xx must fail immediately."""
    calls = {"count": 0}

    def _fake_post(*a, **k):
        calls["count"] += 1
        return _FakeResponse(401, text='{"detail": "invalid api key"}')

    monkeypatch.setattr(httpx, "post", _fake_post)
    provider = ElevenLabsTTSProvider(api_key="bad-key", storage_dir=tmp_path)
    with pytest.raises(RuntimeError):
        provider.synthesize(text="hello", voice_id="voice-1")
    assert calls["count"] == 1


def test_synthesize_wraps_connection_failures_too(tmp_path, monkeypatch):
    """Regression test: this provider previously had zero error handling
    around the network call at all - a bare httpx.ConnectError would have
    propagated completely uncaught instead of becoming a clear,
    diagnosable RuntimeError."""

    def _raise_connect_error(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _raise_connect_error)
    provider = ElevenLabsTTSProvider(api_key="test-key", storage_dir=tmp_path)
    with pytest.raises(RuntimeError, match="ElevenLabs request failed"):
        provider.synthesize(text="hello", voice_id="voice-1")
