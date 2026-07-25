"""A deterministic, zero-dependency LLMClient used in two situations:

1. As the automatic production fallback when no ANTHROPIC_API_KEY is
   configured (config.Settings.resolved_llm_provider) — so the app degrades
   safely (empty/placeholder output, clearly logged) instead of crashing.
2. As the test double for agent unit tests, where `response_builder` is set
   to return a fixed, known JSON payload so agent parsing/logic can be
   asserted deterministically without any network call.
"""

import time
from collections.abc import Callable

from content_factory.llm.base import LLMClient, LLMResponse

ResponseBuilder = Callable[[str, str], str]


def _default_response_builder(system: str, prompt: str) -> str:
    # Agents ask for "a JSON array" or "a JSON object" explicitly in their
    # prompts (see agents/research_agent.py, agents/script_agent.py) — the
    # safe-degradation default mirrors whichever shape was requested rather
    # than guessing content.
    if "json array" in prompt.lower():
        return "[]"
    return "{}"


class FakeLLMClient(LLMClient):
    def __init__(self, response_builder: ResponseBuilder | None = None) -> None:
        self._response_builder = response_builder or _default_response_builder

    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024) -> LLMResponse:
        start = time.monotonic()
        text = self._response_builder(system, prompt)
        duration_ms = max(int((time.monotonic() - start) * 1000), 1)
        return LLMResponse(
            text=text,
            provider="fake",
            model="fake-llm",
            model_version="v1",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            duration_ms=duration_ms,
        )
