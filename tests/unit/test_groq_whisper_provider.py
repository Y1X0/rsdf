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
        import json as _json

        self.text = _json.dumps(json_body)

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


def test_transcribe_sends_a_request_httpx_can_actually_encode_without_crashing(tmp_path, monkeypatch):
    """Regression test for a real production incident, found via a live
    end-to-end run: the previous code passed `data=` as a list of 2-tuples
    (the `requests`-library convention for repeated form fields). httpx
    only routes `data=` through its multipart/form encoder when it is a
    Mapping - a non-Mapping `data` is silently treated as raw `content`
    instead (with an easy-to-miss DeprecationWarning), dropping `files=`
    entirely and then crashing inside httpx's own request.read() with
    `TypeError: sequence item N: expected a bytes-like object, tuple
    found` while trying to join the tuples as byte chunks. Every other
    test in this file monkeypatches httpx.post itself with a fake
    function, which captures the raw data=/files= kwargs but never
    exercises httpx's actual encoder - exactly why this class of bug
    survived every previous test run. This test instead swaps only the
    network transport (httpx.MockTransport), so the real
    encode_request/MultipartStream code path runs for real, and asserts
    on the real wire-level multipart body httpx actually produced."""
    audio_path = tmp_path / "audio.mp4"
    audio_path.write_bytes(b"fake-bytes")

    captured_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(request.read())
        return httpx.Response(200, json=_verbose_json_body())

    real_post = httpx.post

    def _post_via_mock_transport(url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(httpx, "post", _post_via_mock_transport)
    provider = GroqWhisperProvider(api_key="gsk-test-key", model="whisper-large-v3")

    result = provider.transcribe(str(audio_path))

    assert result.text == "hello world"
    assert len(captured_bodies) == 1
    body_text = captured_bodies[0].decode(errors="replace")
    assert 'name="model"' in body_text
    assert "whisper-large-v3" in body_text
    # Both granularities must survive as separate multipart fields - not
    # silently collapsed to just one, and not dropped along with `files=`.
    assert body_text.count('name="timestamp_granularities[]"') == 2
    assert "segment" in body_text
    assert "word" in body_text
    assert 'name="file"; filename="audio.mp4"' in body_text
    assert "fake-bytes" in body_text

    monkeypatch.setattr(httpx, "post", real_post)


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
    # data must be a Mapping (httpx only routes it through its
    # multipart/form encoder when it is one - see
    # test_transcribe_sends_a_request_httpx_can_actually_encode_without_crashing
    # for the regression test proving this at the real-encoding level), with
    # "timestamp_granularities[]" as a list value so it can be sent twice -
    # both "segment" and "word" explicitly requested, not left to an API
    # default.
    assert captured["data"]["model"] == "whisper-large-v3"
    assert captured["data"]["response_format"] == "verbose_json"
    assert captured["data"]["timestamp_granularities[]"] == ["segment", "word"]
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


def test_transcribe_raises_a_runtime_error_carrying_groqs_actual_error_body_on_http_error_status(tmp_path, monkeypatch):
    """Same reasoning as test_groq_provider.py's own regression test: a bare
    httpx.HTTPStatusError's str() carries no hint of *why* (bad key,
    unsupported audio format) - the real response body is the only thing
    that explains it wherever agent_runs.error_message ends up being the
    only record of the failure."""
    audio_path = tmp_path / "audio.mp4"
    audio_path.write_bytes(b"fake-bytes")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(401, {"error": "invalid api key"}))
    provider = GroqWhisperProvider(api_key="bad-key", model="whisper-large-v3")

    with pytest.raises(RuntimeError) as exc_info:
        provider.transcribe(str(audio_path))
    assert "401" in str(exc_info.value)
    assert "invalid api key" in str(exc_info.value)


def test_transcribe_retries_on_5xx_then_succeeds(tmp_path, monkeypatch):
    """PHASE1_AUDIT_v2.md F19 (retry/backoff around external provider
    calls) had never been applied to transcription - a real source-video
    transcription call would fail outright on one transient Groq hiccup
    instead of quietly recovering, the same gap already closed for
    publishing/analytics-ingestion/LLM providers."""
    audio_path = tmp_path / "audio.mp4"
    audio_path.write_bytes(b"fake-bytes")
    calls = {"count": 0}

    def _fake_post(*a, **k):
        calls["count"] += 1
        if calls["count"] < 2:
            return _FakeResponse(503, {})
        return _FakeResponse(200, _verbose_json_body())

    monkeypatch.setattr(httpx, "post", _fake_post)
    provider = GroqWhisperProvider(api_key="gsk-test-key", model="whisper-large-v3")
    result = provider.transcribe(str(audio_path))

    assert result.text == "hello world"
    assert calls["count"] == 2


def test_transcribe_retries_on_timeout_then_succeeds(tmp_path, monkeypatch):
    audio_path = tmp_path / "audio.mp4"
    audio_path.write_bytes(b"fake-bytes")
    calls = {"count": 0}

    def _fake_post(*a, **k):
        calls["count"] += 1
        if calls["count"] < 2:
            raise httpx.TimeoutException("timed out")
        return _FakeResponse(200, _verbose_json_body())

    monkeypatch.setattr(httpx, "post", _fake_post)
    provider = GroqWhisperProvider(api_key="gsk-test-key", model="whisper-large-v3")
    result = provider.transcribe(str(audio_path))

    assert result.text == "hello world"
    assert calls["count"] == 2


def test_transcribe_does_not_retry_on_4xx(tmp_path, monkeypatch):
    """A bad API key or unsupported audio format won't fix itself on
    retry - retrying would just waste attempts, so a 4xx must fail
    immediately."""
    audio_path = tmp_path / "audio.mp4"
    audio_path.write_bytes(b"fake-bytes")
    calls = {"count": 0}

    def _fake_post(*a, **k):
        calls["count"] += 1
        return _FakeResponse(401, {"error": "invalid api key"})

    monkeypatch.setattr(httpx, "post", _fake_post)
    provider = GroqWhisperProvider(api_key="bad-key", model="whisper-large-v3")
    with pytest.raises(RuntimeError):
        provider.transcribe(str(audio_path))
    assert calls["count"] == 1


def test_transcribe_wraps_connection_failures_too(tmp_path, monkeypatch):
    """Regression test: this provider previously had zero error handling
    around the network call at all - a bare httpx.ConnectError would have
    propagated completely uncaught instead of becoming a clear,
    diagnosable RuntimeError."""
    audio_path = tmp_path / "audio.mp4"
    audio_path.write_bytes(b"fake-bytes")

    def _raise_connect_error(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _raise_connect_error)
    provider = GroqWhisperProvider(api_key="gsk-test-key", model="whisper-large-v3")
    with pytest.raises(RuntimeError, match="Groq transcription request failed"):
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
