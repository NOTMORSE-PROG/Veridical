"""Reference-list rules (F3.2): minimum/maximum reference count, and an
APA-ish citation-style sniff. Both are pure functions over `ctx.citations`
(V-006's structured reference list) — no LLM, no network.
"""

import re

from app.checks.rules import RuleContext, RuleOutcome, RuleSpec, register_rule
from app.checks.rules.bounds import Bound, extract_bound
from app.checks.rules.keywords import contains_any, full_text, load_keywords
from app.models.enums import ResultOutcome

_KEYWORDS = load_keywords()

REFERENCE_COUNT_RULE_ID = "reference_count_min_max"
CITATION_STYLE_RULE_ID = "citation_style_sniff"

# APA-ish: an in-text or reference-list year in parentheses, e.g. "(2020)"
# or "(2020a)". Deliberately approximate ("-ish", per the ticket) — a full
# APA validator is out of scope for a structural sniff test.
_APA_YEAR_PATTERN = re.compile(r"\(\d{4}[a-z]?\)")

# backend-critic finding (BUG-048 review): a reference noun within a few
# words AFTER where the bound's own number appears in the text -- catches
# ordinary "at least 5 major references" phrasing, where `bound.unit`
# (bounds.py only ever captures the single word immediately adjacent to
# the number) is the adjective "major", not the noun. Bounded to a few
# words so it doesn't drift into matching an unrelated reference noun
# that happens to appear later in a long evidence string.
_REFERENCE_NOUN_NEARBY = re.compile(
    r"\b(?:reference|references|citation|citations|source|sources)\b"
)


def _matches_reference_count(criterion) -> bool:
    text = full_text(criterion)
    return contains_any(text, _KEYWORDS.reference_nouns) and extract_bound(text) is not None


def _within(kind: str, low: float, high: float | None, actual: float) -> bool:
    if kind == "min":
        return actual >= low
    if kind == "max":
        return actual <= low
    if kind == "range" and high is not None:
        return low <= actual <= high
    return actual == low  # exact


def _bound_is_about_references(bound: Bound, text: str) -> bool:
    """BUG-048 root cause 2: `extract_bound` returns the FIRST numeric bound
    it finds anywhere in the criterion's text+evidence -- on a levelled
    rubric whose evidence spells out per-level sub-constraints ("<= 2
    internet sites" inside a criterion actually about "5 major
    references"), that can be a bound about a completely different
    quantity. A bound with no captured unit word is ambiguous but assumed
    to be about references (the rule only matched because a reference
    noun was already present in the text); a bound whose unit is a real
    word that ISN'T a reference noun ("internet", "pages", ...) is about
    something else and must not be applied to `len(ctx.citations)` --
    UNLESS a reference noun actually appears within a few words of that
    same number in the text (`bound.unit` only ever captures a single
    adjacent word, so "at least 5 major references" needs this fallback
    or it would wrongly read as unrelated too)."""
    if bound.unit is None or bound.unit in _KEYWORDS.reference_nouns:
        return True
    nearby = re.search(rf"\b{re.escape(f'{bound.low:g}')}\b(?:\s+\S+){{0,3}}", text)
    return bool(nearby and _REFERENCE_NOUN_NEARBY.search(nearby.group()))


def _run_reference_count(criterion, ctx: RuleContext) -> RuleOutcome:
    text = full_text(criterion)
    bound = extract_bound(text)
    if bound is None:
        return RuleOutcome(
            outcome=ResultOutcome.not_applicable,
            anchor="reference list",
            detail={
                "reason": "Could not extract a reference-count bound from the criterion wording."
            },
        )
    if not _bound_is_about_references(bound, text):
        return RuleOutcome(
            outcome=ResultOutcome.unverifiable,
            anchor="reference list",
            detail={
                "reason": (
                    f"Found a numeric bound ('{bound.low} {bound.unit}') in this criterion's "
                    "wording, but it doesn't clearly describe the total reference count -- "
                    "applying it to the reference list would risk checking the wrong quantity. "
                    "Needs manual review."
                ),
                "found_unit": bound.unit,
            },
        )
    count = len(ctx.citations)
    ok = _within(bound.kind, bound.low, bound.high, count)
    return RuleOutcome(
        outcome=ResultOutcome.passed if ok else ResultOutcome.failed,
        anchor="reference list",
        detail={
            "expected": {"kind": bound.kind, "low": bound.low, "high": bound.high},
            "actual_count": count,
        },
    )


def _matches_citation_style(criterion) -> bool:
    return contains_any(full_text(criterion), _KEYWORDS.citation_styles)


def _named_style(criterion) -> str | None:
    """backend-critic finding (BUG-048 review): this still had root cause
    1's exact bug -- a bare substring test ("apa" is a substring of
    "apart") -- one function below where `_matches_citation_style` was
    already fixed to word-boundary matching. Also non-deterministic
    before this fix: iterating a `frozenset[str]` visits members in an
    order that depends on Python's string-hash seed
    (`PYTHONHASHSEED`), so which style "won" when a criterion's text
    happened to contain more than one recognized token could differ
    between process restarts -- `sorted()` makes the result the same
    every time, independent of hash seed."""
    lowered = full_text(criterion).lower()
    for style in sorted(_KEYWORDS.citation_styles):
        if re.search(rf"\b{re.escape(style)}\b", lowered):
            return style
    return None


def _run_citation_style(criterion, ctx: RuleContext) -> RuleOutcome:
    style = _named_style(criterion)
    if style != "apa":
        return RuleOutcome(
            outcome=ResultOutcome.unverifiable,
            anchor="reference list",
            detail={
                "reason": (
                    f"Only APA-style (year-in-parentheses) detection is implemented; "
                    f"'{style}' is not yet checked automatically."
                )
            },
        )
    if not ctx.citations:
        return RuleOutcome(
            outcome=ResultOutcome.not_applicable,
            anchor="reference list",
            detail={"reason": "No reference-list entries to check style against."},
        )
    matching = [c for c in ctx.citations if _APA_YEAR_PATTERN.search(c.raw_text)]
    ratio = len(matching) / len(ctx.citations)
    non_matching_examples = [c.raw_text for c in ctx.citations if c not in matching][:3]
    meets_threshold = ratio >= ctx.citation_style_min_ratio
    outcome = ResultOutcome.passed if meets_threshold else ResultOutcome.failed
    return RuleOutcome(
        outcome=outcome,
        anchor="reference list",
        detail={
            "matched_ratio": ratio,
            "threshold": ctx.citation_style_min_ratio,
            "examples_not_matching": non_matching_examples,
        },
    )


register_rule(
    RuleSpec(
        rule_id=REFERENCE_COUNT_RULE_ID,
        description="Compares a stated reference-count bound against the structured citation list.",
        matches=_matches_reference_count,
        run=_run_reference_count,
    )
)
register_rule(
    RuleSpec(
        rule_id=CITATION_STYLE_RULE_ID,
        description="Sniffs an APA-ish year-in-parentheses pattern across the reference list.",
        matches=_matches_citation_style,
        run=_run_citation_style,
    )
)
