"""Caption timing — pure logic, no external dependency, always available.

Real word-level timing comes from the TTS provider when it supplies one
(ElevenLabs does, per-character/word); when it doesn't (e.g. the Silent
provider, or a future provider that omits timings), `even_word_timings`
approximates it by distributing words evenly across the estimated duration.
This is a documented Phase 1 simplification — ARCHITECTURE.md §9's tech
stack section calls out Whisper word-level alignment as the real long-term
answer; swapping the approximation for real alignment later only touches
this module, since callers only ever deal in `WordTiming`/`CaptionCue`.
"""

from dataclasses import dataclass

AVERAGE_WORDS_PER_SECOND = 2.5


@dataclass(frozen=True)
class WordTiming:
    word: str
    start_s: float
    end_s: float


@dataclass(frozen=True)
class CaptionCue:
    text: str
    start_s: float
    end_s: float


def estimate_duration_s(text: str) -> float:
    word_count = max(len(text.split()), 1)
    return round(word_count / AVERAGE_WORDS_PER_SECOND, 2)


def even_word_timings(text: str, total_duration_s: float | None = None) -> list[WordTiming]:
    words = text.split()
    if not words:
        return []
    duration = total_duration_s if total_duration_s else estimate_duration_s(text)
    per_word = duration / len(words)
    timings = []
    for i, word in enumerate(words):
        timings.append(
            WordTiming(word=word, start_s=round(i * per_word, 3), end_s=round((i + 1) * per_word, 3))
        )
    return timings


def build_captions(word_timings: list[WordTiming], max_words_per_cue: int = 4) -> list[CaptionCue]:
    cues: list[CaptionCue] = []
    for i in range(0, len(word_timings), max_words_per_cue):
        chunk = word_timings[i : i + max_words_per_cue]
        if not chunk:
            continue
        cues.append(
            CaptionCue(
                text=" ".join(w.word for w in chunk),
                start_s=chunk[0].start_s,
                end_s=chunk[-1].end_s,
            )
        )
    return cues
