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


def test_complete_raises_on_http_error_status(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(401, {"error": "invalid api key"}))
    client = GroqLLMClient(api_key="bad-key", model="llama-3.3-70b-versatile")
    with pytest.raises(httpx.HTTPStatusError):
        client.complete(system="s", prompt="p")


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
