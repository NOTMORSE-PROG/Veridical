"""Loader for the intent/outcome cue lexicon (ENGINEERING.md §8: the
vocabulary that decides what counts as an objective/finding statement is
data, never a hardcoded branch) — same convention as
`app.checks.rules.keywords.load_keywords`."""

import json
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_FILE = Path(__file__).parent / "data" / "cues.json"


@dataclass(frozen=True)
class AgreementCues:
    objective_heading_synonyms: frozenset[str]
    outcome_heading_synonyms: frozenset[str]
    intent_phrase_cues: tuple[str, ...]
    outcome_phrase_cues: tuple[str, ...]
    future_work_guard_phrases: tuple[str, ...]
    scope_negation_phrases: tuple[str, ...]


def load_cues(path: Path | None = None) -> AgreementCues:
    raw = json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))
    return AgreementCues(
        objective_heading_synonyms=frozenset(raw["objective_heading_synonyms"]),
        outcome_heading_synonyms=frozenset(raw["outcome_heading_synonyms"]),
        intent_phrase_cues=tuple(raw["intent_phrase_cues"]),
        outcome_phrase_cues=tuple(raw["outcome_phrase_cues"]),
        future_work_guard_phrases=tuple(raw["future_work_guard_phrases"]),
        scope_negation_phrases=tuple(raw["scope_negation_phrases"]),
    )
