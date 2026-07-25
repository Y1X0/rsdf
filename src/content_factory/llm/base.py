"""The one interface business logic (agents/*, services/*) is allowed to
depend on for LLM calls (adjustment #6: "every external integration must be
behind an interface. Never call Anthropic ... directly from business
logic"). Swapping providers, or adding a second one, never touches an
agent's code — only llm/factory.py's provider selection changes.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMResponse:
    """Everything adjustment #3 requires us to keep about an AI generation:
    prompt, model, model version, cost, and duration are all present here so
    agents/base.py's AgentRunRecorder can persist them verbatim without any
    agent having to remember to pass them through separately."""

    text: str
    provider: str
    model: str
    model_version: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int


class LLMClient(ABC):
    @abstractmethod
    def complete(self, *, system: str, prompt: str, max_tokens: int = 1024) -> LLMResponse:
        """Send a single-turn completion request and return a structured response."""
        raise NotImplementedError
