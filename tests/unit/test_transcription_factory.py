from content_factory.config import Settings
from content_factory.transcription.factory import get_transcription_provider
from content_factory.transcription.providers.null_provider import NullTranscriptionProvider


def test_falls_back_to_null_when_no_provider_configured():
    settings = Settings(transcription_provider="null")
    provider = get_transcription_provider(settings)
    assert isinstance(provider, NullTranscriptionProvider)


def test_falls_back_to_null_when_groq_selected_with_no_api_key():
    settings = Settings(transcription_provider="groq", groq_api_key="")
    provider = get_transcription_provider(settings)
    assert isinstance(provider, NullTranscriptionProvider)


def test_uses_groq_whisper_when_api_key_present():
    settings = Settings(transcription_provider="groq", groq_api_key="gsk-test-key")
    provider = get_transcription_provider(settings)
    assert type(provider).__name__ == "GroqWhisperProvider"


def test_groq_whisper_uses_configured_model():
    settings = Settings(
        transcription_provider="groq", groq_api_key="gsk-test-key", groq_whisper_model="whisper-large-v3-turbo"
    )
    provider = get_transcription_provider(settings)
    assert provider._model == "whisper-large-v3-turbo"
