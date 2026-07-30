import wave

from content_factory.video_production.tts.providers.silent_provider import SilentTTSProvider


def test_silent_provider_writes_real_wav_file(tmp_path):
    provider = SilentTTSProvider(storage_dir=tmp_path)
    result = provider.synthesize(text="hello world this is a test", voice_id="default")

    assert result.provider == "silent"
    assert result.cost_usd == 0.0
    assert result.duration_s > 0
    assert len(result.word_timings) == 6

    with wave.open(result.audio_path, "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getframerate() == 16_000


def test_silent_provider_word_timings_span_full_duration(tmp_path):
    provider = SilentTTSProvider(storage_dir=tmp_path)
    result = provider.synthesize(text="one two three", voice_id="default")
    assert result.word_timings[0].start_s == 0.0
    assert result.word_timings[-1].end_s == result.duration_s
