"""GroqLLMClient — tested against mocked HTTP responses only, never a live
network call, matching this codebase's zero-secrets-required test
philosophy (the same pattern used for the TikTok/YouTube/Instagram
publishing providers in test_publishing_providers.py)."""

import httpx
import pytest

from content_factory.llm.providers.groq_provider import GroqLLMClient


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict):
        self.status_code = status_code
        self._json_body = json_body
        import json as _json

        self.text = _json.dumps(json_body)

    def json(self) -> dict:
        return self._json_body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def _chat_completion_body(content: str, *, prompt_tokens=100, completion_tokens=50, model="llama-3.3-70b-versatile") -> dict:
    return {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def test_complete_returns_genuine_text_and_marks_provider_as_groq(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(200, _chat_completion_body('{"brief_text": "real content here"}')),
    )
    client = GroqLLMClient(api_key="gsk-test-key", model="llama-3.3-70b-versatile")
    response = client.complete(system="system prompt", prompt="user prompt", max_tokens=512)

    assert response.provider == "groq"
    assert response.text == '{"brief_text": "real content here"}'
    assert response.model == "llama-3.3-70b-versatile"


def test_complete_maps_token_usage_and_computes_a_nonzero_cost(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(200, _chat_completion_body("ok", prompt_tokens=1000, completion_tokens=500))
    )
    client = GroqLLMClient(api_key="gsk-test-key", model="llama-3.3-70b-versatile")
    response = client.complete(system="s", prompt="p")

    assert response.input_tokens == 1000
    assert response.output_tokens == 500
    # Cost tracking must degrade gracefully rather than silently report $0
    # forever if usage ever moves past the free tier — see the provider's
    # own pricing-constant comment.
    assert response.cost_usd > 0


def test_complete_sends_the_configured_model_and_real_messages_shape(monkeypatch):
    captured = {}

    def _fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse(200, _chat_completion_body("ok"))

    monkeypatch.setattr(httpx, "post", _fake_post)
    client = GroqLLMClient(api_key="gsk-test-key", model="llama-3.1-8b-instant")
    client.complete(system="be helpful", prompt="do the thing", max_tokens=777)

    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer gsk-test-key"
    assert captured["json"]["model"] == "llama-3.1-8b-instant"
    assert captured["json"]["max_tokens"] == 777
    assert captured["json"]["messages"] == [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "do the thing"},
    ]


def test_complete_raises_a_runtime_error_carrying_groqs_actual_error_body_on_http_error_status(monkeypatch):
    """Regression test for a real production incident: a bare
    httpx.HTTPStatusError's str() is just "Client error '401 ...'" with no
    hint of *why* (bad key vs decommissioned model vs rate limit) - and
    that's all that ever reached agent_runs.error_message /
    idempotency_records.error_message, with no other way to diagnose a
    failure without direct log/DB access to wherever this is deployed.
    The provider must wrap it with Groq's real response body so the actual
    reason is preserved wherever str(exc) ends up."""
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(400, {"error": {"message": "model `x` has been decommissioned"}})
    )
    client = GroqLLMClient(api_key="bad-key", model="llama-3.3-70b-versatile")
    with pytest.raises(RuntimeError) as exc_info:
        client.complete(system="s", prompt="p")
    assert "400" in str(exc_info.value)
    assert "decommissioned" in str(exc_info.value)


def test_complete_wraps_connection_failures_too(monkeypatch):
    def _raise_connect_error(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _raise_connect_error)
    client = GroqLLMClient(api_key="gsk-test-key", model="llama-3.3-70b-versatile")
    with pytest.raises(RuntimeError, match="Groq API request failed"):
        client.complete(system="s", prompt="p")


def test_complete_retries_on_5xx_then_succeeds(monkeypatch):
    """PHASE1_AUDIT_v2.md F19 (retry/backoff around external provider
    calls) had never been applied to the LLM providers themselves - every
    real script/hook generation call would fail outright on one transient
    Groq hiccup instead of quietly recovering, the same gap already closed
    for the publishing/analytics-ingestion providers."""
    calls = {"count": 0}

    def _fake_post(*a, **k):
        calls["count"] += 1
        if calls["count"] < 2:
            return _FakeResponse(503, {})
        return _FakeResponse(200, _chat_completion_body("recovered"))

    monkeypatch.setattr(httpx, "post", _fake_post)
    client = GroqLLMClient(api_key="gsk-test-key", model="llama-3.3-70b-versatile")
    response = client.complete(system="s", prompt="p")

    assert response.text == "recovered"
    assert calls["count"] == 2


def test_complete_retries_on_timeout_then_succeeds(monkeypatch):
    calls = {"count": 0}

    def _fake_post(*a, **k):
        calls["count"] += 1
        if calls["count"] < 2:
            raise httpx.TimeoutException("timed out")
        return _FakeResponse(200, _chat_completion_body("recovered"))

    monkeypatch.setattr(httpx, "post", _fake_post)
    client = GroqLLMClient(api_key="gsk-test-key", model="llama-3.3-70b-versatile")
    response = client.complete(system="s", prompt="p")

    assert response.text == "recovered"
    assert calls["count"] == 2


def test_complete_does_not_retry_on_4xx(monkeypatch):
    """A bad API key or a decommissioned model won't fix itself on retry -
    retrying would just waste attempts, so a 4xx must fail immediately."""
    calls = {"count": 0}

    def _fake_post(*a, **k):
        calls["count"] += 1
        return _FakeResponse(401, {"error": {"message": "invalid api key"}})

    monkeypatch.setattr(httpx, "post", _fake_post)
    client = GroqLLMClient(api_key="bad-key", model="llama-3.3-70b-versatile")
    with pytest.raises(RuntimeError):
        client.complete(system="s", prompt="p")
    assert calls["count"] == 1


def test_complete_handles_empty_content_without_crashing(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResponse(200, {"model": "llama-3.3-70b-versatile", "choices": [{"message": {"content": None}}], "usage": {}}),
    )
    client = GroqLLMClient(api_key="gsk-test-key", model="llama-3.3-70b-versatile")
    response = client.complete(system="s", prompt="p")
    assert response.text == ""
    assert response.input_tokens == 0
    assert response.output_tokens == 0
