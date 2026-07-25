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
