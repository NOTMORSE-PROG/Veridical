"""Flag evidence/annotation/override (F8.2/F8.4, screen 4i).

Mirrors `app.checks.escalation`'s resolution SHAPE by design (reason
mandatory, audit-logged, triggers a live rescore) — not shared code,
because they operate on different tables for a reason documented in
STATE.md's 2026-07-25 design-call entry: an escalation is uncertainty in
OUR grading process; a flag is "a possible inconsistency" in the
manuscript itself (charter rule 3) — conflating the two data models
would blur that wording precision. Overriding a flag never touches its
parent `check_result.outcome/score` — only the flag's own
`overridden`/`override_reason`, which `app.report.scoring._flag_deduction`
already excludes from the severity deduction once set (V-019, unchanged
by this ticket).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFoundError
from app.flags.schemas import FlagOut
from app.models.audit import AuditLog
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion
from app.models.run import CheckResult, CheckRun, Flag
from app.report.service import aggregate_and_score


async def _scoped_flag(
    session: AsyncSession, flag_id: int, instructor_id: int
) -> tuple[Flag, CheckResult]:
    row = (
        await session.execute(
            select(Flag, CheckResult)
            .join(CheckResult, CheckResult.id == Flag.check_result_id)
            .join(CheckRun, CheckRun.id == CheckResult.check_run_id)
            .join(Manuscript, Manuscript.id == CheckRun.manuscript_id)
            .where(Flag.id == flag_id, Manuscript.instructor_id == instructor_id)
        )
    ).first()
    if row is None:
        raise NotFoundError(f"No flag {flag_id}.")
    return row


async def _to_flag_out(session: AsyncSession, flag: Flag, result: CheckResult) -> FlagOut:
    criterion_text = None
    if result.criterion_id is not None:
        criterion = await session.get(Criterion, result.criterion_id)
        criterion_text = criterion.text if criterion else None
    detail = result.detail or {}
    return FlagOut(
        id=flag.id,
        check_result_id=result.id,
        check_kind=result.kind.value,
        criterion_text=criterion_text,
        severity=flag.severity.value,
        confidence=float(flag.confidence) if flag.confidence is not None else None,
        evidence_excerpt=flag.evidence_excerpt,
        page_anchor=flag.page_anchor,
        annotation=flag.annotation,
        overridden=flag.overridden,
        override_reason=flag.override_reason,
        ai_verdict_summary=detail.get("verdict") or detail.get("basis"),
        ai_reasoning=detail.get("reasoning") or detail.get("reason"),
    )


async def get_flag(session: AsyncSession, flag_id: int, instructor_id: int) -> FlagOut:
    flag, result = await _scoped_flag(session, flag_id, instructor_id)
    return await _to_flag_out(session, flag, result)


async def annotate_flag(
    session: AsyncSession, flag_id: int, instructor_id: int, annotation: str
) -> FlagOut:
    flag, result = await _scoped_flag(session, flag_id, instructor_id)
    flag.annotation = annotation.strip()
    session.add(
        AuditLog(
            event_type="flag_annotated",
            check_run_id=result.check_run_id,
            payload={
                "flag_id": flag_id,
                "check_result_id": result.id,
                "instructor_id": instructor_id,
                "annotation": annotation.strip(),
            },
        )
    )
    await session.commit()
    await session.refresh(flag)
    return await _to_flag_out(session, flag, result)


async def override_flag(
    session: AsyncSession, flag_id: int, instructor_id: int, reason: str
) -> tuple[FlagOut, int]:
    """Overriding a flag never destroys the original AI/check finding
    (ticket AC) — `evidence_excerpt`/`page_anchor`/the parent
    `check_result.detail` are untouched; only `overridden`/
    `override_reason` change, which is what the scoring engine already
    watches (`_flag_deduction`, V-019). Returns the flag plus its
    `check_run_id` so the router can hand back a fresh `ReportOut` in the
    SAME response (ticket AC: "recomputes score/status immediately")."""
    flag, result = await _scoped_flag(session, flag_id, instructor_id)
    flag.overridden = True
    flag.override_reason = reason.strip()
    session.add(
        AuditLog(
            event_type="flag_overridden",
            check_run_id=result.check_run_id,
            agreement_score=flag.confidence,
            payload={
                "flag_id": flag_id,
                "check_result_id": result.id,
                "instructor_id": instructor_id,
                "reason": reason.strip(),
                "severity": flag.severity.value,
            },
        )
    )
    await session.commit()
    await aggregate_and_score(session, result.check_run_id)
    await session.refresh(flag)
    return await _to_flag_out(session, flag, result), result.check_run_id
