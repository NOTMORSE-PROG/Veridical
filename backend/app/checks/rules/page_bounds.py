"""F3.2 page limits: bind a complete requirement to one whole-document count.

This intentionally bounded grammar is not general natural-language parsing.
Unsupported page constraints must not fall through to a different rule family.
Original criterion wording is never normalized in storage or audit.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from app.checks.rules.bounds import Bound, BoundKind
from app.checks.rules.grammar import compile_pattern, expand, read_grammar, strings

_DEFAULT_FILE = Path(__file__).parent / "data" / "page_rule_grammar.json"


@dataclass(frozen=True)
class PageGrammar:
    numbers: dict[str, int]
    compound_first: frozenset[str]
    compound_second: frozenset[str]
    word_separator: re.Pattern[str]
    integer: re.Pattern[str]
    patterns: dict[BoundKind, re.Pattern[str]]
    unit_signals: tuple[re.Pattern[str], ...]
    non_count_patterns: tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class PageBoundParse:
    state: Literal["recognized", "unsupported", "unrelated"]
    bound: Bound | None = None


def _alternatives(values) -> str:
    # An empty configured vocabulary must never become an empty-string match.
    return "|".join(re.escape(value) for value in sorted(values, key=len, reverse=True)) or r"(?!)"


def _expand(pattern: str, values: dict[str, str]) -> str:
    return expand(pattern, values)


@lru_cache
def load_page_grammar(path: Path | None = None) -> PageGrammar:
    """Load packaged data, or an explicit override for a configured caller."""
    raw = read_grammar(path or _DEFAULT_FILE)
    numbers = raw.get("numbers")
    if (
        not isinstance(numbers, dict)
        or not numbers
        or any(
            not isinstance(word, str) or not word.strip() or type(value) is not int or value < 0
            for word, value in numbers.items()
        )
    ):
        raise ValueError("Page capability grammar requires nonnegative integer word values.")
    for key in ("units", "subjects", "compound_first", "compound_second", "unit_signals"):
        strings(raw, key)
    strings(raw, "abbreviated_units", allow_empty=True)
    strings(raw, "non_count_patterns", allow_empty=True)
    for key in ("word_separator", "integer", "prefix", "suffix", "pattern"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ValueError(f"Page capability grammar requires {key}.")
    compile_pattern(raw["integer"])
    comparisons = raw.get("comparisons")
    if (
        not isinstance(comparisons, dict)
        or not comparisons
        or not set(comparisons).issubset({"min", "max", "exact"})
        or any(not isinstance(value, str) or not value.strip() for value in comparisons.values())
    ):
        raise ValueError("Page capability grammar has invalid comparator kinds.")
    if not set(raw["abbreviated_units"]).issubset(raw["units"]) or not (
        set(raw["compound_first"]) | set(raw["compound_second"])
    ).issubset(numbers):
        raise ValueError("Page capability grammar has inconsistent vocabulary.")
    words = _alternatives(raw["numbers"])
    number = rf"(?:{raw['integer']}|(?:{words})(?:{raw['word_separator']}(?:{words}))?)"
    units = set(raw["units"])
    abbreviations = units.intersection(raw["abbreviated_units"])
    values = {
        "subjects": _alternatives(raw["subjects"]),
        "units": _alternatives(units),
        "long_units": _alternatives(units - abbreviations),
        "abbreviated_units": _alternatives(abbreviations),
        "number": number,
        "suffix": raw["suffix"],
    }
    values["prefix"] = _expand(raw["prefix"], values)
    patterns = {
        kind: compile_pattern(
            _expand(raw["pattern"], {**values, "comparison": comparison}), groups=("number", "unit")
        )
        for kind, comparison in raw["comparisons"].items()
    }
    return PageGrammar(
        numbers=raw["numbers"],
        compound_first=frozenset(raw["compound_first"]),
        compound_second=frozenset(raw["compound_second"]),
        word_separator=re.compile(raw["word_separator"]),
        integer=re.compile(raw["integer"]),
        patterns=patterns,
        unit_signals=tuple(
            compile_pattern(_expand(pattern, values)) for pattern in raw["unit_signals"]
        ),
        non_count_patterns=tuple(
            compile_pattern(_expand(pattern, values)) for pattern in raw["non_count_patterns"]
        ),
    )


def _number(text: str, grammar: PageGrammar) -> int | None:
    if grammar.integer.fullmatch(text):
        try:
            return int(text)
        except ValueError:
            return None
    if text in grammar.numbers:
        return grammar.numbers[text]
    parts = grammar.word_separator.split(text)
    if (
        len(parts) == 2
        and parts[0] in grammar.compound_first
        and parts[1] in grammar.compound_second
    ):
        return grammar.numbers[parts[0]] + grammar.numbers[parts[1]]
    return None


def parse_page_bound(text: str, *, grammar: PageGrammar | None = None) -> PageBoundParse:
    grammar = grammar or load_page_grammar()
    normalized = " ".join(text.lower().split())
    for kind, pattern in grammar.patterns.items():
        match = pattern.fullmatch(normalized)
        if not match:
            continue
        value = _number(match["number"], grammar)
        if value is not None:
            return PageBoundParse(
                "recognized", Bound(kind=kind, low=value, high=None, unit=match["unit"])
            )
    if not any(pattern.search(normalized) for pattern in grammar.unit_signals):
        return PageBoundParse("unrelated")
    if any(pattern.fullmatch(normalized) for pattern in grammar.non_count_patterns):
        return PageBoundParse("unrelated")
    return PageBoundParse("unsupported")
