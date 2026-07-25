"""Composition root for LLMClient. This is the only place that decides which
concrete provider gets used — agents and services always receive an
LLMClient instance through dependency injection (see api/deps.py), never by
calling this factory or a provider class themselves mid-logic. That
separation is what makes adjustment #6 ("never call Anthropic directly from
business logic") mechanically enforced rather than just a convention.
"""

from content_factory.config import Settings
from content_factory.llm.base import LLMClient
from content_factory.llm.providers.fake_provider import FakeLLMClient
from content_factory.logging_config import get_logger

logger = get_logger(__name__)


def get_llm_client(settings: Settings) -> LLMClient:
    provider = settings.resolved_llm_provider()

    if provider == "anthropic":
        from content_factory.llm.providers.anthropic_provider import AnthropicLLMClient

        return AnthropicLLMClient(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            model_version=settings.anthropic_model_version,
        )

    if provider == "fake":
        logger.warning(
            "llm_provider_fallback",
            reason="no_api_key_or_explicit_fake",
            configured_provider=settings.llm_provider,
        )
        return FakeLLMClient()

    raise ValueError(f"Unknown LLM provider: {provider!r}")
