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
    session: AsyncSession, check_run_id: int, kind: CheckKind, extraction: ExtractionResult
) -> CheckResult | None:
    detail = source_limitation_detail(extraction)
    if detail is None:
        return None
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
