"""The only module in the codebase allowed to import the `anthropic` SDK.

Everything else reaches an LLM through content_factory.llm.base.LLMClient,
obtained via content_factory.llm.factory.get_llm_client — never through this
module directly (adjustment #6).
"""

import time

import anthropic

from content_factory.llm.base import LLMClient, LLMResponse

# Illustrative per-million-token pricing, USD. Kept as a plain constant
# rather than a live pricing API call — good enough for Phase 1 cost
# tracking, and easy to update as a one-line change if pricing changes.
# See ARCHITECTURE.md §18's "validate against current vendor pricing" note.
_PRICE_PER_MTOK_INPUT_USD = 3.0
_PRICE_PER_MTOK_OUTPUT_USD = 15.0


class AnthropicLLMClient(LLMClient):
    def __init__(self, api_key: str, model: str, model_version: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._model_version = model_version

    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024) -> LLMResponse:
        start = time.monotonic()
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        text = "".join(block.text for block in response.content if block.type == "text")
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost_usd = (
            input_tokens / 1_000_000 * _PRICE_PER_MTOK_INPUT_USD
            + output_tokens / 1_000_000 * _PRICE_PER_MTOK_OUTPUT_USD
        )

        return LLMResponse(
            text=text,
            provider="anthropic",
            model=self._model,
            model_version=self._model_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost_usd, 6),
            duration_ms=duration_ms,
        )
