"""Prevent a named-section instruction from being settled by a global inventory."""

import re
from functools import lru_cache
from pathlib import Path

from app.checks.rules.grammar import alternatives, compile_pattern, expand, read_grammar, strings
from app.checks.rules.keywords import claim_text
from app.checks.rules.numerals import normalize_numerals
from app.checks.rules.presence import is_presence_claim

_DEFAULT_FILE = Path(__file__).parent / "data" / "claim_scope_grammar.json"
WORD_COUNT_RULE_ID = "section_word_count"


@lru_cache
def load_claim_grammar(path: Path | None = None):
    raw = read_grammar(path or _DEFAULT_FILE)
    values = {
        "documents": alternatives(strings(raw, "document_subjects")),
        "references": alternatives(strings(raw, "reference_subjects")),
        "units": alternatives(strings(raw, "reference_units")),
        "adjectives": alternatives(strings(raw, "count_adjectives")),
        "word_units": alternatives(strings(raw, "word_units")),
    }
    number = raw.get("number")
    compile_pattern(number)
    bound = expand(raw.get("bound"), {"number": number})
    compile_pattern(bound)
    values["bound"] = bound
    grammar = {
        key: tuple(compile_pattern(expand(pattern, values)) for pattern in strings(raw, key))
        for key in ("reference_count_patterns", "citation_patterns")
    }
    # Word-count claims bind the criterion's own named section, so they are
    # expanded per target at match time (`_word_count_patterns`).
    word_patterns = strings(raw, "word_count_patterns")
    for pattern in word_patterns:
        if pattern.count("{{target}}") != 1:
            raise ValueError("A word-count capability must bind exactly one target.")
        compile_pattern(expand(pattern, {**values, "target": "heading"}))
    grammar["word_count_templates"] = tuple(word_patterns)
    grammar["values"] = values
    return grammar


@lru_cache
def _word_count_patterns(target: str) -> tuple[re.Pattern[str], ...]:
    grammar = load_claim_grammar()
    values = {**grammar["values"], "target": re.escape(target)}
    return tuple(
        compile_pattern(expand(pattern, values)) for pattern in grammar["word_count_templates"]
    )


def _claim(criterion) -> str:
    return normalize_numerals(" ".join(claim_text(criterion).split()))


def complete_reference_claim(criterion, rule_id: str) -> bool:
    patterns = load_claim_grammar()[
        "reference_count_patterns" if rule_id == "reference_count_min_max" else "citation_patterns"
    ]
    text = _claim(criterion)
    return any(pattern.fullmatch(text) for pattern in patterns)


def complete_word_count_claim(criterion, target: str | None) -> bool:
    if not isinstance(target, str) or not target.strip():
        return False
    text = _claim(criterion)
    return any(pattern.fullmatch(text) for pattern in _word_count_patterns(target))


def allowed_named_section_rules(criterion, target: str | None) -> frozenset[str] | None:
    grammar = load_claim_grammar()
    if target is None:
        return None
    if is_presence_claim(criterion, target):
        return frozenset({"required_section_present"})
    text = _claim(criterion)
    allowed = {
        rule_id
        for rule_id, key in (
            ("reference_count_min_max", "reference_count_patterns"),
            ("citation_style_sniff", "citation_patterns"),
        )
        if any(pattern.fullmatch(text) for pattern in grammar[key])
    }
    if complete_word_count_claim(criterion, target):
        allowed.add(WORD_COUNT_RULE_ID)
    return frozenset(allowed)
