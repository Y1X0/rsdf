from content_factory.config import Settings
from content_factory.diarization.factory import get_diarization_provider
from content_factory.diarization.providers.null_provider import NullDiarizationProvider


def test_falls_back_to_null_when_no_provider_configured():
    settings = Settings(diarization_provider="null")
    provider = get_diarization_provider(settings)
    assert isinstance(provider, NullDiarizationProvider)


def test_falls_back_to_null_when_pyannote_selected_with_no_huggingface_token():
    settings = Settings(diarization_provider="pyannote", huggingface_token="")
    provider = get_diarization_provider(settings)
    assert isinstance(provider, NullDiarizationProvider)


def test_uses_pyannote_when_huggingface_token_present():
    settings = Settings(diarization_provider="pyannote", huggingface_token="hf_test_token")
    provider = get_diarization_provider(settings)
    assert type(provider).__name__ == "PyannoteDiarizationProvider"
    assert provider._hf_token == "hf_test_token"
