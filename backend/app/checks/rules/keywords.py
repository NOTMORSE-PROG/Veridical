"""Loader for the structural-rule keyword data file (ENGINEERING.md §8:
the vocabulary that decides which rule a criterion is talking about is
data, never a hardcoded branch)."""

import json
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_FILE = Path(__file__).parent / "data" / "structural_keywords.json"


@dataclass(frozen=True)
class StructuralKeywords:
    citation_styles: frozenset[str]
    reference_nouns: frozenset[str]
    page_nouns: frozenset[str]
    margin_nouns: frozenset[str]
    spacing_nouns: frozenset[str]
    table_nouns: frozenset[str]
    table_format_nouns: frozenset[str]
    order_nouns: frozenset[str]
    section_reference_nouns: frozenset[str]


def load_keywords(path: Path | None = None) -> StructuralKeywords:
    raw = json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))
    return StructuralKeywords(
        citation_styles=frozenset(raw["citation_styles"]),
        reference_nouns=frozenset(raw["reference_nouns"]),
        page_nouns=frozenset(raw["page_nouns"]),
        margin_nouns=frozenset(raw["margin_nouns"]),
        spacing_nouns=frozenset(raw["spacing_nouns"]),
        table_nouns=frozenset(raw["table_nouns"]),
        table_format_nouns=frozenset(raw["table_format_nouns"]),
        order_nouns=frozenset(raw["order_nouns"]),
        section_reference_nouns=frozenset(raw["section_reference_nouns"]),
    )


def contains_any(text: str, words: frozenset[str]) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in words)


def full_text(criterion) -> str:
    """Every rule matches/evaluates against text + evidence together — the
    instructor's stated evidence is as much "what the criterion means" as
    its headline text."""
    return f"{criterion.text} {criterion.evidence or ''}"
