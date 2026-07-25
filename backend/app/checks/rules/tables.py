"""Table-formatting-presence rule (F3.2): tables exist, and most of them
carry a caption. Pure function over `ctx.tables` (V-004/V-007's native +
vision-recovered table inventory).
"""

from app.checks.rules import RuleContext, RuleOutcome, RuleSpec, register_rule
from app.checks.rules.keywords import contains_any, full_text, load_keywords
from app.ingest.schemas import TableBlock
from app.models.enums import ResultOutcome

_KEYWORDS = load_keywords()

RULE_ID = "table_formatting_presence"


def _matches(criterion) -> bool:
    text = full_text(criterion)
    return contains_any(text, _KEYWORDS.table_nouns) and contains_any(
        text, _KEYWORDS.table_format_nouns
    )


def _anchor(table: TableBlock) -> str:
    if table.page is not None:
        return f"page {table.page}"
    if table.paragraph is not None:
        return f"paragraph {table.paragraph}"
    return "document"


def _run(criterion, ctx: RuleContext) -> RuleOutcome:
    if not ctx.tables:
        return RuleOutcome(
            outcome=ResultOutcome.failed,
            anchor="document",
            detail={"reason": "No tables were found in the manuscript."},
        )
    captioned = [t for t in ctx.tables if t.caption]
    ratio = len(captioned) / len(ctx.tables)
    if ratio >= ctx.table_caption_min_ratio:
        return RuleOutcome(
            outcome=ResultOutcome.passed,
            anchor=_anchor(ctx.tables[0]),
            detail={"total_tables": len(ctx.tables), "captioned": len(captioned), "ratio": ratio},
        )
    uncaptioned = next(t for t in ctx.tables if not t.caption)
    return RuleOutcome(
        outcome=ResultOutcome.failed,
        anchor=_anchor(uncaptioned),
        detail={
            "reason": "Fewer than the expected share of tables carry a caption.",
            "total_tables": len(ctx.tables),
            "captioned": len(captioned),
            "ratio": ratio,
            "threshold": ctx.table_caption_min_ratio,
        },
    )


register_rule(
    RuleSpec(
        rule_id=RULE_ID,
        description="Checks that tables are present and mostly carry captions.",
        matches=_matches,
        run=_run,
    )
)
