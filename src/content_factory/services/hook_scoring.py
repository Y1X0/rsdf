"""Hook framework taxonomy + a pure-Python hook-strength scorer.

Scope and honesty note (read before extending this module): this encodes
two kinds of *publicly available* knowledge, not a reverse-engineering of
any platform's private ranking model:

1. `HOOK_FRAMEWORKS` is a curated set of well-established, widely-taught
   short-form copywriting patterns (curiosity gap, pattern interrupt,
   direct callout, etc.) - the same kind of framework taught by countless
   public creator-economy resources, not a secret. It exists so
   ScriptAgent/ClipSelectionAgent's prompts can ask an LLM for a *named,
   proven pattern* instead of "write a punchy hook" and hope for the best.

2. `score_hook_strength()` is a small, explainable heuristic over
   independently-documented retention signals - Instagram/Meta has
   publicly stated (via its own Creator blog and public statements from
   its leadership) that its ranking systems weigh session/watch time,
   likes, comments, shares/sends, saves, and completion/replay behavior.
   Nobody outside the platform has access to the actual private ranking
   model, and this module does not claim to. What it *can* do, honestly,
   is estimate - before any real view ever happens - how likely a hook is
   to earn those same publicly-known signals (does it stop the scroll,
   invite a reply, promise something specific), using the same kind of
   text-feature heuristics real headline-analysis tools (e.g. CoSchedule's
   or Sharethrough's headline analyzers) use. It is a pre-publish proxy,
   not a guarantee - `content_intelligence.record_hook_outcome` still
   records the real, post-publish viral score, which is the actual ground
   truth this proxy is trying to anticipate.

Deliberately pure Python, zero new dependencies (no nltk/textstat): a
hook is 3-15 words, not a document - a syllable-counting NLP library is
more dependency weight than this analysis needs, and this codebase's own
established philosophy throughout is to avoid heavy dependencies where a
plain heuristic does the job just as well.
"""

import re
from dataclasses import dataclass, field

# Each framework is a *named, provable* short-form hook pattern - given to
# the LLM as a menu with a one-line example, not a vague instruction to
# "be punchy". `key` is what agents store in Script.hook_framework /
# Clip.hook_framework.
HOOK_FRAMEWORKS: dict[str, dict[str, str]] = {
    "curiosity_gap": {
        "name": "Curiosity Gap",
        "description": "Opens a specific, named knowledge gap the viewer wants closed immediately.",
        "example": "The one mistake that's costing you followers.",
    },
    "direct_callout": {
        "name": "Direct Callout",
        "description": "Names the exact viewer/situation so the right audience self-selects instantly.",
        "example": "If you post daily and still get no views, watch this.",
    },
    "pattern_interrupt": {
        "name": "Pattern Interrupt",
        "description": "Opens mid-action or with an unexpected statement that breaks scroll momentum.",
        "example": "Stop scrolling - this changes everything.",
    },
    "bold_claim": {
        "name": "Bold Claim",
        "description": "States a strong, specific, testable claim upfront.",
        "example": "This is the fastest way to double your engagement.",
    },
    "numbered_tease": {
        "name": "Numbered Tease",
        "description": "Promises a specific, countable list, so the viewer stays to see all of it.",
        "example": "3 things nobody tells you about growing an account.",
    },
    "relatable_problem": {
        "name": "Relatable Problem",
        "description": "Names a specific pain point the audience recognizes instantly as their own.",
        "example": "Tired of posting every day and getting zero views?",
    },
    "shock_stat": {
        "name": "Shock Stat",
        "description": "Leads with a surprising, specific number or fact.",
        "example": "90% of viewers decide to leave in the first 3 seconds.",
    },
    "cliffhanger": {
        "name": "Cliffhanger",
        "description": "Promises a specific payoff later in the video without revealing it yet.",
        "example": "Wait until you see what happens at the end.",
    },
    "second_person_question": {
        "name": "Second-Person Question",
        "description": "Asks a direct question addressed straight at the viewer.",
        "example": "Do you make this mistake every time you post?",
    },
    "contrarian": {
        "name": "Contrarian",
        "description": "Challenges a belief the audience assumes is true.",
        "example": "Everything you've been told about hooks is wrong.",
    },
}

# A curated, non-exhaustive wordlist of terms that reliably show up in
# high-performing short-form hooks across the curiosity-gap/pattern-
# interrupt/shock-stat frameworks above - the same style of curated list
# real headline-analysis tools use, not a claim of completeness.
#
# Arabic entries alongside the English ones (not a separate/replacement
# list) - this platform's actual hooks are overwhelmingly Arabic/Levantine
# colloquial (see ScriptAgent/ClipSelectionAgent's real usage), and an
# English-only wordlist silently scored every real Arabic hook at 0 on
# these two sub-scores regardless of quality. Same "curated, non-
# exhaustive" bar as the English list, not a claim of completeness.
_CURIOSITY_MARKERS = (
    "secret", "mistake", "nobody tells you", "here's why", "here's what",
    "the truth about", "stop", "never", "always", "one thing", "wait until",
    "wait for it", "you won't believe", "this is why", "turns out",
    "سر", "السر", "غلطة", "الغلطة", "محدش", "ما حدا", "لسا ما", "الحقيقة",
    "الحقيقه", "طلع", "توقف", "استنى", "ستنى", "خلص", "اكتشف", "اكتشفت",
    "شو صار", "ليش", "مين قالك",
)
_POWER_WORDS = (
    "shocking", "proven", "instantly", "finally", "warning", "exclusive",
    "guaranteed", "easy", "free", "surprising", "secret", "banned", "insane",
    "صادم", "صادمة", "مثبت", "مضمون", "فورا", "حصري", "تحذير", "مجاني",
    "سهل", "خطير", "جنون", "جنوني", "مفاجأة", "مفاجئ", "ممنوع",
)

# \S+ (whitespace-delimited tokens) rather than an alpha-only character
# class: the previous [a-zA-Z']+ only ever matched Latin script, so it
# silently counted 0 words in any Arabic hook - length_score (25% of the
# composite) was always 0 regardless of actual length. This is a rougher
# per-language word count (punctuation can stay glued to a token) but
# works the same way regardless of script, which matters more here.
_WORD_RE = re.compile(r"\S+")
# Arabic second-person pronouns alongside the English regex - "you/your"
# alone matched nothing in Arabic text, so second_person_score (15% of
# the composite) was always 0 for Arabic hooks regardless of phrasing.
_SECOND_PERSON_RE = re.compile(
    r"\byou(r|rself)?\b|أنت[ِمكِ]?|انت[ِمو]?ا?|إنت[ِمو]?ا?", re.IGNORECASE
)
_DIGIT_RE = re.compile(r"\d")
_QUESTION_MARK_RE = re.compile(r"[?؟]\s*$")

# A hook is spoken/read in roughly the first 1-3 seconds of a short-form
# video - long enough to say ~5-12 words at a natural pace, not a whole
# sentence. Word counts outside this band are actively penalized rather
# than just unrewarded: too short reads as low-content, too long is
# already-too-slow to land before the viewer decides to scroll past.
_IDEAL_WORD_RANGE = (4, 14)


@dataclass(frozen=True)
class HookScoreResult:
    overall: float  # 0-100 composite
    length_score: float
    question_score: float
    second_person_score: float
    number_score: float
    curiosity_marker_score: float
    power_word_score: float
    notes: list[str] = field(default_factory=list)


def _length_score(word_count: int) -> float:
    if word_count == 0:
        return 0.0
    low, high = _IDEAL_WORD_RANGE
    if low <= word_count <= high:
        return 1.0
    distance = (low - word_count) if word_count < low else (word_count - high)
    return max(0.0, 1.0 - distance / high)


def _marker_score(text_lower: str, markers: tuple[str, ...]) -> float:
    hits = sum(1 for marker in markers if marker in text_lower)
    return min(1.0, hits / 2.0)  # 2+ hits already maxes this sub-score out


def score_hook_strength(hook_text: str) -> HookScoreResult:
    """Pre-publish proxy score (0-100) for a hook's estimated ability to
    earn the same publicly-known engagement/retention signals platforms
    have said they weigh (watch time, replies, shares) - see this module's
    own docstring for exactly what is and isn't being claimed here."""
    text = (hook_text or "").strip()
    text_lower = text.lower()
    words = _WORD_RE.findall(text)
    word_count = len(words)

    length_score = _length_score(word_count)
    question_score = 1.0 if _QUESTION_MARK_RE.search(text) else 0.0
    second_person_score = 1.0 if _SECOND_PERSON_RE.search(text) else 0.0
    number_score = 1.0 if _DIGIT_RE.search(text) else 0.0
    curiosity_marker_score = _marker_score(text_lower, _CURIOSITY_MARKERS)
    power_word_score = _marker_score(text_lower, _POWER_WORDS)

    overall = 100 * (
        0.25 * length_score
        + 0.15 * question_score
        + 0.15 * second_person_score
        + 0.15 * number_score
        + 0.20 * curiosity_marker_score
        + 0.10 * power_word_score
    )

    notes: list[str] = []
    if word_count == 0:
        notes.append("Empty hook text.")
    elif not (_IDEAL_WORD_RANGE[0] <= word_count <= _IDEAL_WORD_RANGE[1]):
        notes.append(
            f"{word_count} words - a spoken hook lands best around "
            f"{_IDEAL_WORD_RANGE[0]}-{_IDEAL_WORD_RANGE[1]} words."
        )
    if question_score == 0.0:
        notes.append("Not phrased as a question - questions tend to invite replies/comments.")
    if second_person_score == 0.0:
        notes.append('No direct address ("you"/"your") - direct address tends to raise relevance.')
    if number_score == 0.0:
        notes.append("No number - a specific count or stat often reads as more credible/concrete.")
    if curiosity_marker_score == 0.0:
        notes.append("No curiosity-gap language detected (e.g. \"the truth about\", \"nobody tells you\").")

    return HookScoreResult(
        overall=round(overall, 2),
        length_score=round(length_score, 4),
        question_score=question_score,
        second_person_score=second_person_score,
        number_score=number_score,
        curiosity_marker_score=round(curiosity_marker_score, 4),
        power_word_score=round(power_word_score, 4),
        notes=notes,
    )


def format_hook_frameworks_for_prompt() -> str:
    """Renders HOOK_FRAMEWORKS as a menu an LLM prompt can include
    directly - one line per framework, name + description + example."""
    lines = []
    for key, framework in HOOK_FRAMEWORKS.items():
        lines.append(f'- "{key}" ({framework["name"]}): {framework["description"]} Example: "{framework["example"]}"')
    return "\n".join(lines)
