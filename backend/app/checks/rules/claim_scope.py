"""Prevent a named-section instruction from being settled by a global inventory."""

from functools import lru_cache
from pathlib import Path

from app.checks.rules.grammar import alternatives, compile_pattern, expand, read_grammar, strings
from app.checks.rules.keywords import full_text
from app.checks.rules.presence import is_presence_claim

_DEFAULT_FILE = Path(__file__).parent / "data" / "claim_scope_grammar.json"


@lru_cache
def load_claim_grammar(path: Path | None = None):
    raw = read_grammar(path or _DEFAULT_FILE)
    values = {
        "documents": alternatives(strings(raw, "document_subjects")),
        "references": alternatives(strings(raw, "reference_subjects")),
        "units": alternatives(strings(raw, "reference_units")),
        "adjectives": alternatives(strings(raw, "count_adjectives")),
    }
    number = raw.get("number")
    compile_pattern(number)
    bound = expand(raw.get("bound"), {"number": number})
    compile_pattern(bound)
    values["bound"] = bound
    return {
        key: tuple(compile_pattern(expand(pattern, values)) for pattern in strings(raw, key))
        for key in ("reference_count_patterns", "citation_patterns")
    }


def complete_reference_claim(criterion, rule_id: str) -> bool:
    patterns = load_claim_grammar()[
        "reference_count_patterns" if rule_id == "reference_count_min_max" else "citation_patterns"
    ]
    text = " ".join(full_text(criterion).split())
    return any(pattern.fullmatch(text) for pattern in patterns)


def allowed_named_section_rules(criterion, target: str | None) -> frozenset[str] | None:
    grammar = load_claim_grammar()
    if target is None:
        return None
    if is_presence_claim(criterion, target):
        return frozenset({"required_section_present"})
    text = " ".join(full_text(criterion).split())
    return frozenset(
        rule_id
        for rule_id, key in (
            ("reference_count_min_max", "reference_count_patterns"),
            ("citation_style_sniff", "citation_patterns"),
        )
        if any(pattern.fullmatch(text) for pattern in grammar[key])
    )
