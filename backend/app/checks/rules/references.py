"""Reference-list rules (F3.2): minimum/maximum reference count, and an
APA-ish citation-style sniff. Both are pure functions over `ctx.citations`
(V-006's structured reference list) — no LLM, no network.
"""

import re

from app.checks.rules import RuleContext, RuleOutcome, RuleSpec, register_rule
from app.checks.rules.bounds import extract_bound
from app.checks.rules.keywords import contains_any, full_text, load_keywords
from app.models.enums import ResultOutcome

_KEYWORDS = load_keywords()

REFERENCE_COUNT_RULE_ID = "reference_count_min_max"
CITATION_STYLE_RULE_ID = "citation_style_sniff"

# APA-ish: an in-text or reference-list year in parentheses, e.g. "(2020)"
# or "(2020a)". Deliberately approximate ("-ish", per the ticket) — a full
# APA validator is out of scope for a structural sniff test.
_APA_YEAR_PATTERN = re.compile(r"\(\d{4}[a-z]?\)")


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
    lowered = full_text(criterion).lower()
    for style in _KEYWORDS.citation_styles:
        if style in lowered:
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
