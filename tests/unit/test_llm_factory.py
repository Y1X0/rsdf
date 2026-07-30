from content_factory.config import Settings
from content_factory.llm.factory import get_llm_client
from content_factory.llm.providers.fake_provider import FakeLLMClient


def test_falls_back_to_fake_when_no_api_key_configured():
    settings = Settings(llm_provider="anthropic", anthropic_api_key="")
    client = get_llm_client(settings)
    assert isinstance(client, FakeLLMClient)


def test_uses_anthropic_when_api_key_present():
    settings = Settings(llm_provider="anthropic", anthropic_api_key="sk-test-key")
    client = get_llm_client(settings)
    assert type(client).__name__ == "AnthropicLLMClient"


def test_falls_back_to_fake_when_groq_selected_with_no_api_key():
    """Groq (a free-tier alternative added when a paid Anthropic account
    wasn't available) follows the exact same safe-default contract as
    every other provider — no key, no real client, ever."""
    settings = Settings(llm_provider="groq", groq_api_key="")
    client = get_llm_client(settings)
    assert isinstance(client, FakeLLMClient)


def test_uses_groq_when_api_key_present():
    settings = Settings(llm_provider="groq", groq_api_key="gsk-test-key")
    client = get_llm_client(settings)
    assert type(client).__name__ == "GroqLLMClient"


def test_groq_client_uses_configured_model():
    settings = Settings(llm_provider="groq", groq_api_key="gsk-test-key", groq_model="llama-3.1-8b-instant")
    client = get_llm_client(settings)
    assert client._model == "llama-3.1-8b-instant"
