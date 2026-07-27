from content_factory.services.hook_scoring import (
    HOOK_FRAMEWORKS,
    format_hook_frameworks_for_prompt,
    score_hook_strength,
)


def test_a_hook_using_several_proven_signals_scores_much_higher_than_a_generic_one():
    strong = score_hook_strength("Do you make this mistake with your first 3 seconds?")
    weak = score_hook_strength("Check out my new video")

    assert strong.overall > weak.overall
    assert strong.overall >= 70
    assert weak.overall <= 40


def test_question_format_is_detected():
    assert score_hook_strength("Is this the biggest mistake creators make?").question_score == 1.0
    assert score_hook_strength("This is the biggest mistake creators make.").question_score == 0.0


def test_second_person_address_is_detected():
    assert score_hook_strength("Your first video was probably like this").second_person_score == 1.0
    assert score_hook_strength("Most first videos look like this").second_person_score == 0.0


def test_number_presence_is_detected():
    assert score_hook_strength("3 things nobody tells you about hooks").number_score == 1.0
    assert score_hook_strength("Things nobody tells you about hooks").number_score == 0.0


def test_curiosity_marker_detection():
    result = score_hook_strength("Here's the truth about why your hooks fail")
    assert result.curiosity_marker_score > 0.0


def test_empty_hook_scores_at_the_bottom_with_an_explanatory_note():
    result = score_hook_strength("")
    assert result.overall == 0.0
    assert any("Empty" in note for note in result.notes)


def test_very_long_hook_is_penalized_for_length():
    long_hook = " ".join(["word"] * 40)
    short_ideal_hook = "Do you make this one common mistake today?"
    assert score_hook_strength(long_hook).length_score < score_hook_strength(short_ideal_hook).length_score


def test_hook_frameworks_taxonomy_is_well_formed():
    assert len(HOOK_FRAMEWORKS) >= 8
    for key, framework in HOOK_FRAMEWORKS.items():
        assert framework["name"]
        assert framework["description"]
        assert framework["example"]
        # Keys are what gets stored in Script.hook_framework/Clip.hook_framework -
        # must be stable, machine-usable identifiers.
        assert key == key.lower()
        assert " " not in key


def test_format_hook_frameworks_for_prompt_includes_every_framework():
    rendered = format_hook_frameworks_for_prompt()
    for key in HOOK_FRAMEWORKS:
        assert key in rendered
