"""Tier-0 deterministic signal layer (D-011, F3.2 ticket note): cheap,
explainable heuristics computed alongside semantic grading (V-017 wires
this in when it builds a semantic check_result). SHADOW MODE ONLY
(D-012): every signal here is recorded on the result for future promotion
evaluation (V-025 owns the promotion table) — nothing in this module ever
decides a criterion's real outcome. A verdict produced by these signals
alone must be labeled `"basis": "heuristic"` if it is ever surfaced
(charter honesty rule: never dress a heuristic up as an LLM judgment).

Scope note: D-011 also names "section coherence via embedding
similarity" as a Tier-0 signal. That one is deliberately NOT implemented
here — it needs a real embedding model, and V-036 hasn't picked one yet
(potion-base-8M, per D-011, but not wired up until then). Faking
coherence with a placeholder (e.g. raw word overlap) would misrepresent
what "basis": "heuristic" means for this specific signal, so it's an
honest gap, not a hack — recorded as a TODO(V-036), not built early.
"""

import re
from dataclasses import dataclass
from typing import Any

from app.config import Settings, get_settings

_WORD_PATTERN = re.compile(r"[A-Za-z']+")
_SENTENCE_END_PATTERN = re.compile(r"[.!?]+")
_VOWEL_GROUP_PATTERN = re.compile(r"[aeiouy]+", re.IGNORECASE)
# Generic parenthetical citation shape ("(Smith, 2020)", "(2020)") shared
# with app/checks/rules/references.py's APA-ish sniff — intentionally
# permissive here since this signal is informational, not a pass/fail gate.
_CITATION_LIKE_PATTERN = re.compile(r"\([^()]{0,60}\d{4}[a-z]?\)")


@dataclass(frozen=True)
class ShadowSignals:
    """Every value is a plain float so the whole thing is JSON-serializable
    straight into `check_result.detail.shadow` (V-017's job to store)."""

    basis: str
    readability_flesch: float
    vocab_diversity_ttr: float
    citation_density_per_1000_words: float
    word_count: int
    shadow_verdict: str  # "pass" | "fail" | "inconclusive" — never auto-decides (D-012)


def _count_syllables(word: str) -> int:
    groups = _VOWEL_GROUP_PATTERN.findall(word)
    count = len(groups)
    if word.lower().endswith("e") and count > 1:
        count -= 1
    return max(count, 1)


def _flesch_reading_ease(words: list[str], sentence_count: int) -> float:
    word_count = len(words)
    if word_count == 0 or sentence_count == 0:
        return 0.0
    syllable_count = sum(_count_syllables(w) for w in words)
    return 206.835 - 1.015 * (word_count / sentence_count) - 84.6 * (syllable_count / word_count)


def compute_shadow_signals(text: str, settings: Settings | None = None) -> ShadowSignals:
    """Pure function of the section text a semantic criterion is graded
    against — no LLM, no network, deterministic (same input -> same
    output), matching every other rule in this package."""
    settings = settings or get_settings()
    words = _WORD_PATTERN.findall(text)
    word_count = len(words)
    sentence_count = max(len(_SENTENCE_END_PATTERN.findall(text)), 1)

    readability = _flesch_reading_ease(words, sentence_count)
    unique_ratio = len({w.lower() for w in words}) / word_count if word_count else 0.0
    citation_hits = len(_CITATION_LIKE_PATTERN.findall(text))
    density = (citation_hits / word_count) * 1000 if word_count else 0.0

    if word_count == 0:
        verdict = "inconclusive"
    else:
        readable = (
            settings.signal_readability_flesch_min
            <= readability
            <= settings.signal_readability_flesch_max
        )
        diverse = unique_ratio >= settings.signal_vocab_diversity_min
        verdict = "pass" if readable and diverse else "fail"

    return ShadowSignals(
        basis="heuristic",
        readability_flesch=readability,
        vocab_diversity_ttr=unique_ratio,
        citation_density_per_1000_words=density,
        word_count=word_count,
        shadow_verdict=verdict,
    )


def shadow_signals_as_detail(signals: ShadowSignals) -> dict[str, Any]:
    """Shape V-017 folds into `check_result.detail.shadow` — a plain dict,
    never a dataclass, so it round-trips through JSONB untouched."""
    return {
        "basis": signals.basis,
        "readability_flesch": signals.readability_flesch,
        "vocab_diversity_ttr": signals.vocab_diversity_ttr,
        "citation_density_per_1000_words": signals.citation_density_per_1000_words,
        "word_count": signals.word_count,
        "shadow_verdict": signals.shadow_verdict,
    }
