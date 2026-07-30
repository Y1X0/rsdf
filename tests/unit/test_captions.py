from content_factory.video_production.captions import (
    build_captions,
    estimate_duration_s,
    even_word_timings,
)


def test_estimate_duration_scales_with_word_count():
    short = estimate_duration_s("one two three")
    long = estimate_duration_s(" ".join(["word"] * 30))
    assert long > short


def test_even_word_timings_covers_full_duration():
    timings = even_word_timings("the quick brown fox jumps", total_duration_s=10.0)
    assert len(timings) == 5
    assert timings[0].start_s == 0.0
    assert timings[-1].end_s == 10.0


def test_even_word_timings_empty_text_returns_empty_list():
    assert even_word_timings("", total_duration_s=10.0) == []


def test_build_captions_chunks_into_cues():
    timings = even_word_timings("one two three four five six seven eight", total_duration_s=8.0)
    cues = build_captions(timings, max_words_per_cue=4)
    assert len(cues) == 2
    assert cues[0].text == "one two three four"
    assert cues[1].text == "five six seven eight"
    assert cues[0].start_s == 0.0
    assert cues[-1].end_s == 8.0
