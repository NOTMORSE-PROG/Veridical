"""Loader for the structural-rule keyword data file (ENGINEERING.md §8:
the vocabulary that decides which rule a criterion is talking about is
data, never a hardcoded branch)."""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
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
    if path is None:
        from app.config import get_settings

        path = get_settings().structural_keywords_file
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


_DEFAULT_KEYWORDS = load_keywords(_DEFAULT_FILE)


def current_keywords() -> StructuralKeywords:
    from app.config import get_settings

    configured = get_settings().structural_keywords_file
    return load_keywords(configured) if configured is not None else _DEFAULT_KEYWORDS


def contains_any(text: str, words: frozenset[str]) -> bool:
    """Word-boundary match, not a bare substring test (BUG-048): a
    criterion whose evidence quotes a rubric's own level name
    ("Acceptable") must never match the word "table" just because
    "table" is a substring of "acceptable". `\\b` treats an internal
    apostrophe/hyphen as a boundary too, which is what a keyword list of
    single tokens ("table", "references", ...) needs."""
    lowered = text.lower()
    return any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in words)


def full_text(criterion) -> str:
    """Every rule matches/evaluates against text + evidence together — the
    instructor's stated evidence is as much "what the criterion means" as
    its headline text."""
    return f"{criterion.text} {criterion.evidence or ''}"


_METHOD_FILE = Path(__file__).parent / "data" / "evidence_method_vocabulary.json"
_TOKEN = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)*")
# Anything that is neither a word character nor whitespace nor plain sentence
# punctuation (comparison symbols, percent signs) can carry a requirement.
_SYMBOL = re.compile(r"[^\w\s.,;:'\"()-]")


@lru_cache
def _vocabulary(path: Path = _METHOD_FILE) -> tuple[frozenset[str], frozenset[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return (
        frozenset(word.lower() for word in raw["words"]),
        frozenset(word.lower() for word in raw["comparison_words"]),
    )


def _without_section_names(text: str) -> str:
    """Remove whole section names only ("the reference list", "works
    cited"); their single words alone are not method vocabulary."""
    from app.checks.rules.sections import section_names

    lowered = text.lower()
    for name in sorted(section_names(), key=len, reverse=True):
        lowered = re.sub(rf"\b{re.escape(name)}\b", " ", lowered)
    return lowered


def evidence_restates_text(criterion) -> bool:
    """True when the evidence sentence only says how a reader checks the
    criterion: no comparison symbol; every number is the text's own; every
    word is the text's own, part of a whole section name, or packaged method
    vocabulary; and comparison words appear only when the evidence states
    exactly the text's own bound. Absent evidence trivially restates."""
    from app.checks.rules.bounds import extract_bound
    from app.checks.rules.numerals import normalize_numerals

    evidence = (getattr(criterion, "evidence", None) or "").strip()
    if not evidence:
        return True
    if _SYMBOL.search(evidence):
        return False
    text = normalize_numerals(criterion.text)
    normalized_evidence = normalize_numerals(evidence)
    text_tokens = set(_TOKEN.findall(text.lower()))
    evidence_tokens = _TOKEN.findall(_without_section_names(normalized_evidence))
    method, comparison = _vocabulary()
    if comparison & set(evidence_tokens):
        stated, required = extract_bound(normalized_evidence), extract_bound(text)
        if (
            stated is None
            or required is None
            or (stated.kind, stated.low, stated.high)
            != (required.kind, required.low, required.high)
        ):
            return False
    allowed = text_tokens | method | comparison
    return all(
        token in text_tokens if token[0].isdigit() else token in allowed
        for token in evidence_tokens
    )


def claim_text(criterion) -> str:
    """The wording a complete-claim grammar must cover, and the only wording
    a rule may read its requirement from: the criterion text alone when its
    evidence only restates it, otherwise text + evidence (so an added
    condition still leaves the claim incomplete)."""
    if evidence_restates_text(criterion):
        return criterion.text
    return full_text(criterion)
