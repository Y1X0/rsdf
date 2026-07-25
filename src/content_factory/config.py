"""Application configuration, loaded entirely from environment variables.

No secret ever has a hardcoded default here — API keys default to an empty
string, and every provider factory (llm/factory.py, video_production/*/factory.py)
treats "no key configured" as an explicit, supported state (falling back to a
fake/silent/null provider) rather than raising at import time. This lets the
whole application and test suite run with zero real credentials.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = "sqlite:///./var/content_factory.db"

    llm_provider: str = "anthropic"  # "anthropic" | "fake"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    anthropic_model_version: str = "2026-02-01"

    tts_provider: str = "silent"  # "elevenlabs" | "silent"
    elevenlabs_api_key: str = ""

    renderer_backend: str = "null"  # "template_pillow" | "null"
    media_storage_dir: str = "./var/media"

    def resolved_llm_provider(self) -> str:
        """Fall back to the fake provider if no key is configured, regardless
        of what LLM_PROVIDER says — prevents the app crashing on missing
        secrets and makes "no key" a first-class, testable state."""
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            return "fake"
        return self.llm_provider

    def resolved_tts_provider(self) -> str:
        if self.tts_provider == "elevenlabs" and not self.elevenlabs_api_key:
            return "silent"
        return self.tts_provider

    def media_storage_path(self) -> Path:
        path = Path(self.media_storage_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
