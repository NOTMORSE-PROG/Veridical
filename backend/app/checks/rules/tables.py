"""Table-formatting-presence rule (F3.2): tables exist, and most of them
carry a caption. Pure function over `ctx.tables` (V-004/V-007's native +
vision-recovered table inventory).
"""

import json
from pathlib import Path

from app.checks.rules import RuleContext, RuleOutcome, RuleSpec, register_rule
from app.checks.rules.keywords import contains_any, full_text, load_keywords
from app.ingest.schemas import TableBlock
from app.models.enums import ResultOutcome

_KEYWORDS = load_keywords()

RULE_ID = "table_formatting_presence"
_COPY = json.loads((Path(__file__).parent / "data" / "table_rule_copy.json").read_text("utf-8"))


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
        # BUG-175: `ctx.tables` empty does NOT always mean "this
        # manuscript has no tables". `ctx.has_images` is the deciding
        # signal, not `vision_status` alone -- live-reproduced against
        # real production data (`backend/data/4.extraction.json` vs
        # `26.extraction.json`, the SAME source document, same 19
        # embedded images): one processing pass landed on
        # `vision_status="none"` (`select_images`'s own size-area floor/
        # count cap filtered every image out of selection that time) and
        # the other on `"done"` (12 tables found) -- config/threshold
        # drift between ingestion times, not a property of the document.
        # A manuscript with genuinely NO embedded images (`has_images`
        # False) is a real, conclusive absence regardless of
        # `vision_status` -- native PDF tables (BUG-163, `ingest/pdf.py`)
        # are unaffected by vision either way and still populate
        # `ctx.tables` when they exist, so this branch is reached only
        # when there is genuinely nothing else to go on.
        #
        # backend review finding (BUG-175 review, live-reproduced):
        # this ambiguity is PDF-only -- `ingest/vision.py` is "PDF-only
        # for now" by its own docstring, and DOCX images are always built
        # with `bbox=None` (`ingest/docx.py`), so `select_images`'s own
        # size-area filter can NEVER pass a DOCX image and `vision_status`
        # can NEVER become anything but "none" for DOCX, regardless of
        # quota or config. Without this `anchor_kind` guard, `has_images`
        # would have made `table_formatting_presence` return
        # `not_applicable` for nearly every DOCX manuscript with any
        # embedded image and no native table -- permanently, not just on
        # a bad day -- silently losing this criterion's ability to ever
        # fail a DOCX manuscript at all. DOCX keeps the old, correct,
        # native-table-only `failed` path unconditionally.
        #
        # When those images were not read for tables, the rule cannot tell a
        # manuscript without tables from one whose tables are pictures. It
        # escalates to the instructor rather than failing the criterion, and
        # never drops out as not applicable: a criterion left out of the
        # composite would quietly raise the readiness band of a thesis whose
        # tables are uncaptioned screenshots.
        if ctx.anchor_kind == "page" and ctx.has_images and ctx.vision_status != "done":
            return RuleOutcome(
                outcome=ResultOutcome.escalated,
                anchor="document",
                detail={"reason": _COPY["images_not_read"]},
            )
        return RuleOutcome(
            outcome=ResultOutcome.failed,
            anchor="document",
            detail={"reason": _COPY["no_tables"]},
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
            "reason": _COPY["too_few_captions"],
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
