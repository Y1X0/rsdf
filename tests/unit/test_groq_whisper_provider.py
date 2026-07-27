"""GroqWhisperProvider - tested against mocked HTTP responses only, never a
live network call, matching this codebase's zero-secrets-required test
philosophy (same pattern as test_groq_provider.py)."""

import httpx
import pytest

from content_factory.transcription.providers.groq_whisper_provider import GroqWhisperProvider


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict):
        self.status_code = status_code
        self._json_body = json_body

    def json(self) -> dict:
        return self._json_body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def _verbose_json_body(*, text="hello world", duration=12.5, include_words=True):
    body = {
        "text": text,
        "duration": duration,
        "segments": [
            {"start": 0.0, "end": 3.0, "text": "hello"},
            {"start": 3.0, "end": 6.0, "text": "world"},
        ],
    }
    if include_words:
        body["words"] = [
            {"start": 0.0, "end": 1.2, "word": "hello"},
            {"start": 1.2, "end": 3.0, "word": "world"},
        ]
    return body


def test_transcribe_returns_genuine_text_and_marks_provider_as_groq(tmp_path, monkeypatch):
    audio_path = tmp_path / "audio.mp4"
    audio_path.write_bytes(b"not-a-real-video-just-test-bytes")

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(200, _verbose_json_body()))
    provider = GroqWhisperProvider(api_key="gsk-test-key", model="whisper-large-v3")

    result = provider.transcribe(str(audio_path))

    assert result.provider == "groq"
    assert result.text == "hello world"
    assert len(result.segments) == 2
    assert result.segments[0].start_s == 0.0
    assert result.segments[0].end_s == 3.0
    assert result.segments[0].text == "hello"
    assert result.duration_s == 12.5
    assert result.cost_usd > 0
    assert len(result.words) == 2
    assert result.words[0].word == "hello"
    assert result.words[0].start_s == 0.0
    assert result.words[0].end_s == 1.2
    assert result.words[1].word == "world"


def test_transcribe_sends_the_configured_model_and_real_multipart_shape(tmp_path, monkeypatch):
    audio_path = tmp_path / "audio.mp4"
    audio_path.write_bytes(b"fake-bytes")
    captured = {}

    def _fake_post(url, *, headers, data, files, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["files"] = files
        return _FakeResponse(200, _verbose_json_body())

    monkeypatch.setattr(httpx, "post", _fake_post)
    provider = GroqWhisperProvider(api_key="gsk-test-key", model="whisper-large-v3")
    provider.transcribe(str(audio_path))

    assert captured["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert captured["headers"]["Authorization"] == "Bearer gsk-test-key"
    # data is a list of tuples (not a dict) specifically so
    # "timestamp_granularities[]" can be sent twice - both "segment" and
    # "word" explicitly requested, not left to an API default.
    assert ("model", "whisper-large-v3") in captured["data"]
    assert ("response_format", "verbose_json") in captured["data"]
    assert ("timestamp_granularities[]", "segment") in captured["data"]
    assert ("timestamp_granularities[]", "word") in captured["data"]
    assert "file" in captured["files"]


def test_transcribe_handles_missing_words_without_crashing(tmp_path, monkeypatch):
    """Word-level timing is a best-effort extra, not a hard requirement -
    a response with segments but no words array at all must still work."""
    audio_path = tmp_path / "audio.mp4"
    audio_path.write_bytes(b"fake-bytes")
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(200, _verbose_json_body(include_words=False))
    )
    provider = GroqWhisperProvider(api_key="gsk-test-key", model="whisper-large-v3")

    result = provider.transcribe(str(audio_path))

    assert len(result.segments) == 2
    assert result.words == []


def test_transcribe_skips_a_malformed_word_item_instead_of_crashing(tmp_path, monkeypatch):
    body = _verbose_json_body()
    body["words"].append({"start": 5.0})  # missing "end" and "word"
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(200, body))
    audio_path = tmp_path / "audio.mp4"
    audio_path.write_bytes(b"fake-bytes")
    provider = GroqWhisperProvider(api_key="gsk-test-key", model="whisper-large-v3")

    result = provider.transcribe(str(audio_path))

    assert len(result.words) == 2  # the 2 well-formed ones survive; the bad one is skipped


def test_transcribe_raises_on_http_error_status(tmp_path, monkeypatch):
    audio_path = tmp_path / "audio.mp4"
    audio_path.write_bytes(b"fake-bytes")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(401, {"error": "invalid api key"}))
    provider = GroqWhisperProvider(api_key="bad-key", model="whisper-large-v3")

    with pytest.raises(httpx.HTTPStatusError):
        provider.transcribe(str(audio_path))


def test_transcribe_handles_missing_segments_without_crashing(tmp_path, monkeypatch):
    audio_path = tmp_path / "audio.mp4"
    audio_path.write_bytes(b"fake-bytes")
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(200, {"text": "", "segments": []})
    )
    provider = GroqWhisperProvider(api_key="gsk-test-key", model="whisper-large-v3")

    result = provider.transcribe(str(audio_path))

    assert result.text == ""
    assert result.segments == []
    assert result.duration_s is None
    assert result.cost_usd == 0.0
