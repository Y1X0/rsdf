"""Groq LLM provider — a free-tier alternative to Anthropic, added when a
paid Anthropic account wasn't available for the pilot's first run. This
does not replace Anthropic (`anthropic_provider.py` is unchanged and still
fully supported via `LLM_PROVIDER=anthropic`); it's a second real
implementation of the same `LLMClient` interface, selected the same way
every other provider is (`llm/factory.py`, keyed off `Settings`).

Groq's API is OpenAI-compatible (`/openai/v1/chat/completions`), so this
needs no new SDK dependency — only `httpx` (the `groq` extra: `pip install
'.[groq]'`), imported lazily here exactly like every other real provider
in this codebase (`elevenlabs_provider.py`, the TikTok/YouTube/Instagram
providers) rather than adding a heavier `openai`/`groq` client library for
one endpoint shape.
"""

import time

from content_factory.llm.base import LLMClient, LLMResponse

_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"

# Illustrative per-million-token pricing, USD, for llama-3.3-70b-versatile
# on Groq's pay-as-you-go tier — same "plain constant, easy to update"
# approach as anthropic_provider.py's own pricing constants. Deliberately
# not assumed to be $0: if pilot usage ever exceeds Groq's free tier and
# moves to metered billing, cost tracking should degrade gracefully
# (report a real, non-zero cost) rather than silently under-report actual
# spend forever. Verify against Groq's current pricing page before relying
# on this for anything beyond illustrative Cost Control Layer tracking.
_PRICE_PER_MTOK_INPUT_USD = 0.59
_PRICE_PER_MTOK_OUTPUT_USD = 0.79


class GroqLLMClient(LLMClient):
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024) -> LLMResponse:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "GroqLLMClient requires the 'groq' extra: pip install '.[groq]'"
            ) from exc

        start = time.monotonic()
        try:
            response = httpx.post(
                _CHAT_COMPLETIONS_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=60.0,
            )
        except httpx.RequestError as exc:
            raise RuntimeError(f"Groq API request failed: {exc}") from exc
        duration_ms = int((time.monotonic() - start) * 1000)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Groq's error body (e.g. "model_decommissioned", "rate_limit
            # exceeded", "invalid_api_key") is the one piece of information
            # that actually explains a failure — httpx's own exception
            # message is just the bare status code/reason phrase, which is
            # useless for diagnosing *why* from agent_runs.error_message or
            # idempotency_records.error_message (both just store str(exc))
            # without direct access to this process's logs.
            raise RuntimeError(f"Groq API error (HTTP {response.status_code}): {response.text}") from exc
        payload = response.json()

        text = payload["choices"][0]["message"]["content"] or ""
        usage = payload.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        cost_usd = (
            input_tokens / 1_000_000 * _PRICE_PER_MTOK_INPUT_USD
            + output_tokens / 1_000_000 * _PRICE_PER_MTOK_OUTPUT_USD
        )

        return LLMResponse(
            text=text,
            provider="groq",
            model=self._model,
            model_version=payload.get("model", self._model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost_usd, 6),
            duration_ms=duration_ms,
        )
