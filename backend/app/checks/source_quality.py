"""Fail closed on persisted source-reading limitations before analytical checks."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_event
from app.ingest.quality import (
    SOURCE_READING_ORDER_NOTE,
    SOURCE_READING_ORDER_UNRESOLVED,
    reading_order_issues,
)
from app.ingest.schemas import ExtractionResult
from app.models.enums import CheckKind, ResultOutcome
from app.models.run import CheckResult

SOURCE_READING_ORDER_PARTIAL = "source_reading_order_partial"


def source_limitation_detail(extraction: ExtractionResult) -> dict | None:
    issues = reading_order_issues(extraction)
    if not issues:
        return None
    return {
        "coverage_reason": SOURCE_READING_ORDER_UNRESOLVED,
        "analysis_coverage": "unsupported",
        "reason": SOURCE_READING_ORDER_NOTE,
        "source_issues": issues,
        "n_source_pages_unassessed": len({i["page"] for i in issues if "page" in i}),
        "n_flags": 0,
    }


async def persist_source_limitation(
    session: AsyncSession,
    check_run_id: int,
    kind: CheckKind,
    extraction: ExtractionResult,
    *,
    supplemental_detail: dict | None = None,
) -> CheckResult | None:
    detail = source_limitation_detail(extraction)
    if detail is None:
        return None
    # The source gate stays authoritative over any supplemental inventory.
    detail = {**(supplemental_detail or {}), **detail}
    if kind == CheckKind.originality_reuse:
        detail.update(corpus="library", comparison_ran=False)
    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=None,
        kind=kind,
        outcome=ResultOutcome.escalated,
        detail=detail,
    )
    session.add(result)
    await write_audit_event(
        session,
        event_type=f"{kind.value}_check_computed",
        check_run_id=check_run_id,
        payload={**detail, "outcome": result.outcome.value},
    )
    await session.commit()
    return result


def unread_page_scope(extraction: ExtractionResult, max_unread_ratio: float) -> list[int] | None:
    """Pages to leave out of an analysis when their reading order is the
    only thing unresolved; None when the limitation cannot be confined.

    Provenance and metadata defects anywhere still block the whole document
    (the scoped `reading_order_issues` call keeps validating every page), as
    does any issue without a page or more unread pages than the ratio."""
    issues = reading_order_issues(extraction)
    if not issues or extraction.page_count < 1:
        return None
    unread = {issue.get("page") for issue in issues}
    if None in unread or len(unread) / extraction.page_count > max_unread_ratio:
        return None
    readable = set(range(1, extraction.page_count + 1)) - unread
    if not readable or reading_order_issues(extraction, pages=readable):
        return None
    return sorted(unread)


def readable_pages_copy(
    extraction: ExtractionResult, max_unread_ratio: float
) -> tuple[ExtractionResult, list[int]]:
    """What the integrity checks are given (BUG-507): every page whose reading
    order is established, and the pages left out. The pipeline and the accuracy
    harness both call this, so a measured figure describes the check as it runs."""
    unread = unread_page_scope(extraction, max_unread_ratio) or []
    return (without_unread_pages(extraction, unread) if unread else extraction), unread


def without_unread_pages(extraction: ExtractionResult, pages: list[int]) -> ExtractionResult:
    """A copy for analyses that read every page except `pages` (from
    `unread_page_scope`). Their text is dropped; their tables are kept but
    marked unreliable so table positions stay stable and no check reads them."""
    unread = set(pages)
    return extraction.model_copy(
        update={
            "blocks": [block for block in extraction.blocks if block.page not in unread],
            "tables": [
                table.model_copy(update={"low_confidence": True}) if table.page in unread else table
                for table in extraction.tables
            ],
            "unread_pages": sorted(unread),
        }
    )


def unread_pages_detail(pages: list[int], detail: dict) -> dict:
    """Coverage fields for a check that read every page except `pages`; a
    more specific limit the check already recorded is kept."""
    return {
        "unread_pages": pages,
        "n_source_pages_unassessed": len(pages),
        "coverage_reason": detail.get("coverage_reason") or SOURCE_READING_ORDER_PARTIAL,
        "analysis_coverage": "unsupported"
        if detail.get("analysis_coverage") == "unsupported"
        else "partial",
    }
