"""Margin/spacing rule (F3.2): compares a criterion's stated margin against
the document's declared page (PDF) or section (DOCX) geometry. Pure
function, no LLM/network — same as every rule in this package.
"""

from app.checks.rules import RuleContext, RuleOutcome, RuleSpec, register_rule
from app.checks.rules.bounds import Bound, extract_bound, to_points
from app.checks.rules.keywords import contains_any, full_text, load_keywords
from app.ingest.schemas import PageGeometry
from app.models.enums import ResultOutcome

_KEYWORDS = load_keywords()

RULE_ID = "margins_spacing"


def _geometry_anchor(geo: PageGeometry) -> str:
    if geo.page is not None:
        return f"page {geo.page}"
    if geo.docx_section is not None:
        return f"document section {geo.docx_section}"
    return "document"


def _matches(criterion) -> bool:
    text = full_text(criterion)
    if not contains_any(text, _KEYWORDS.margin_nouns):
        return False
    bound = extract_bound(text)
    if bound is None or bound.unit is None:
        return False
    return to_points(bound.low, bound.unit) is not None


def _within(actual: float, expected: float, tolerance: float) -> bool:
    return abs(actual - expected) <= tolerance


def _bound_to_points(bound: Bound) -> tuple[float, float | None] | None:
    if bound.unit is None:
        return None
    low_pts = to_points(bound.low, bound.unit)
    if low_pts is None:
        return None
    high_pts = to_points(bound.high, bound.unit) if bound.high is not None else None
    return low_pts, high_pts


def _margin_ok(
    bound: Bound, low_pts: float, high_pts: float | None, value: float, tol: float
) -> bool:
    if bound.kind == "min":
        return value >= low_pts - tol
    if bound.kind == "max":
        return value <= low_pts + tol
    if bound.kind == "range" and high_pts is not None:
        return low_pts - tol <= value <= high_pts + tol
    return _within(value, low_pts, tol)  # exact


def _run(criterion, ctx: RuleContext) -> RuleOutcome:
    text = full_text(criterion)
    bound = extract_bound(text)
    if bound is None or bound.unit is None:
        return RuleOutcome(
            outcome=ResultOutcome.not_applicable,
            anchor="document",
            detail={
                "reason": "Could not extract a margin value with units from the criterion wording."
            },
        )
    converted = _bound_to_points(bound)
    if converted is None:
        return RuleOutcome(
            outcome=ResultOutcome.not_applicable,
            anchor="document",
            detail={"reason": f"Unit '{bound.unit}' is not a recognized length unit."},
        )
    low_pts, high_pts = converted
    with_margins = [geo for geo in ctx.geometry if geo.margins is not None]
    if not with_margins:
        return RuleOutcome(
            outcome=ResultOutcome.not_applicable,
            anchor="document",
            detail={"reason": "No page/section margin geometry is available for this document."},
        )
    tol = ctx.margin_tolerance_pts
    for geo in with_margins:
        left, top, right, bottom = geo.margins  # type: ignore[misc]
        sides = (("left", left), ("top", top), ("right", right), ("bottom", bottom))
        for side_name, value in sides:
            if not _margin_ok(bound, low_pts, high_pts, value, tol):
                return RuleOutcome(
                    outcome=ResultOutcome.failed,
                    anchor=_geometry_anchor(geo),
                    detail={
                        "reason": (
                            f"{side_name.capitalize()} margin is {value:.1f}pt, which does not "
                            f"meet the required {bound.low}{bound.unit} ({bound.kind})."
                        ),
                        "side": side_name,
                        "actual_pts": value,
                        "expected": {"kind": bound.kind, "value": bound.low, "unit": bound.unit},
                    },
                )
    return RuleOutcome(
        outcome=ResultOutcome.passed,
        anchor=_geometry_anchor(with_margins[0]),
        detail={
            "checked_pages_or_sections": len(with_margins),
            "expected": {"kind": bound.kind, "value": bound.low, "unit": bound.unit},
        },
    )


register_rule(
    RuleSpec(
        rule_id=RULE_ID,
        description="Compares a stated margin requirement against declared page/section geometry.",
        matches=_matches,
        run=_run,
    )
)
