"""Page-limit rule (F3.2): compares a criterion's stated page bound
against the manuscript's page count. DOCX has no fixed pages until
rendered (F1 contract, ExtractionResult.page_count == 0 for DOCX) — this
rule is honestly `not_applicable` there, never a fake pass/fail.
"""

from app.checks.rules import RuleContext, RuleOutcome, RuleSpec, register_rule
from app.checks.rules.bounds import extract_bound
from app.checks.rules.keywords import contains_any, full_text, load_keywords
from app.models.enums import ResultOutcome

_KEYWORDS = load_keywords()

RULE_ID = "page_limit"


def _matches(criterion) -> bool:
    text = full_text(criterion)
    return contains_any(text, _KEYWORDS.page_nouns) and extract_bound(text) is not None


def _within(kind: str, low: float, high: float | None, actual: float) -> bool:
    if kind == "min":
        return actual >= low
    if kind == "max":
        return actual <= low
    if kind == "range" and high is not None:
        return low <= actual <= high
    return actual == low  # exact


def _run(criterion, ctx: RuleContext) -> RuleOutcome:
    if ctx.anchor_kind != "page":
        return RuleOutcome(
            outcome=ResultOutcome.not_applicable,
            anchor="document",
            detail={
                "reason": (
                    "Page count is not meaningful for this file format "
                    "(no fixed pages until rendered)."
                )
            },
        )
    text = full_text(criterion)
    bound = extract_bound(text)
    if bound is None:
        return RuleOutcome(
            outcome=ResultOutcome.not_applicable,
            anchor="document",
            detail={"reason": "Could not extract a page bound from the criterion wording."},
        )
    ok = _within(bound.kind, bound.low, bound.high, ctx.page_count)
    anchor = f"document ({ctx.page_count} pages)"
    detail = {
        "expected": {"kind": bound.kind, "low": bound.low, "high": bound.high},
        "actual_pages": ctx.page_count,
    }
    return RuleOutcome(
        outcome=ResultOutcome.passed if ok else ResultOutcome.failed, anchor=anchor, detail=detail
    )


register_rule(
    RuleSpec(
        rule_id=RULE_ID,
        description="Compares a stated page-count bound against the manuscript's page count.",
        matches=_matches,
        run=_run,
    )
)
