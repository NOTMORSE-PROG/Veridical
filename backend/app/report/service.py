"""Aggregation service (F8.1): projects a check_run's persisted
check_results/flags into `ScorableResult`/`ScorableFlag`, runs the pure
`score_check_run`, and persists the composite/status onto the run's
`ReadinessReport` (created once, updated on re-aggregation — e.g. after
an instructor resolves an escalation).
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.escalation import EscalatedItem, list_escalated, resolve_escalation
from app.config import Settings, get_settings
from app.errors import ConflictError, NotFoundError
from app.models.enums import CheckRunStatus
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, Flag, ReadinessReport
from app.report.schemas import (
    CriterionResultOut,
    EscalatedItemOut,
    EvidenceItem,
    ReportOut,
    ResolveEscalationOut,
)
from app.report.scoring import (
    ScorableFlag,
    ScorableResult,
    score_check_run,
    scoring_result_as_dict,
)


async def _load_scorable_results(session: AsyncSession, check_run_id: int) -> list[ScorableResult]:
    rows = (
        await session.execute(
            select(CheckResult, Criterion.weight)
            .join(Criterion, Criterion.id == CheckResult.criterion_id)
            .where(CheckResult.check_run_id == check_run_id)
        )
    ).all()
    return [
        ScorableResult(
            criterion_id=result.criterion_id,
            weight=float(weight),
            outcome=result.outcome,
            score=float(result.score) if result.score is not None else None,
        )
        for result, weight in rows
    ]


async def _load_scorable_flags(session: AsyncSession, check_run_id: int) -> list[ScorableFlag]:
    rows = (
        (
            await session.execute(
                select(Flag)
                .join(CheckResult, CheckResult.id == Flag.check_result_id)
                .where(CheckResult.check_run_id == check_run_id)
            )
        )
        .scalars()
        .all()
    )
    return [ScorableFlag(severity=flag.severity, overridden=flag.overridden) for flag in rows]


async def aggregate_and_score(
    session: AsyncSession, check_run_id: int, settings: Settings | None = None
) -> ReadinessReport:
    settings = settings or get_settings()
    results = await _load_scorable_results(session, check_run_id)
    flags = await _load_scorable_flags(session, check_run_id)
    scoring = score_check_run(results, flags, settings)

    report = await session.scalar(
        select(ReadinessReport).where(ReadinessReport.check_run_id == check_run_id)
    )
    composite = (
        Decimal(str(round(scoring.composite_score, 2)))
        if scoring.composite_score is not None
        else None
    )
    if report is None:
        report = ReadinessReport(
            check_run_id=check_run_id, composite_score=composite, status=scoring.status
        )
        session.add(report)
    else:
        report.composite_score = composite
        report.status = scoring.status
    await session.commit()
    await session.refresh(report)
    return report


async def build_report_payload(
    session: AsyncSession, check_run_id: int, settings: Settings | None = None
) -> dict[str, Any]:
    """Recomputes the full explainable breakdown fresh from the current
    check_results/flags (V-020's `GET /runs/{id}/report` calls this) — the
    SAME pure function that produced the persisted composite/status, so
    the two can never silently disagree."""
    settings = settings or get_settings()
    results = await _load_scorable_results(session, check_run_id)
    flags = await _load_scorable_flags(session, check_run_id)
    return scoring_result_as_dict(score_check_run(results, flags, settings))


def _to_criterion_result(result: CheckResult, criterion: Criterion) -> CriterionResultOut:
    detail = result.detail or {}
    evidence = [EvidenceItem(**item) for item in detail.get("evidence", [])]
    return CriterionResultOut(
        criterion_id=criterion.id,
        text=criterion.text,
        type=criterion.type.value,
        weight=float(criterion.weight),
        kind=result.kind.value,
        outcome=result.outcome.value,
        score=float(result.score) if result.score is not None else None,
        basis=detail.get("basis"),
        anchor=detail.get("anchor"),
        reasoning=detail.get("reasoning"),
        reason=detail.get("reason"),
        evidence=evidence,
    )


async def _owned_check_run(
    session: AsyncSession, check_run_id: int, instructor_id: int
) -> CheckRun:
    check_run = await session.scalar(
        select(CheckRun)
        .join(Manuscript, Manuscript.id == CheckRun.manuscript_id)
        .where(CheckRun.id == check_run_id, Manuscript.instructor_id == instructor_id)
    )
    if check_run is None:
        raise NotFoundError(f"No check run with id {check_run_id}.")
    return check_run


async def get_report(session: AsyncSession, check_run_id: int, instructor_id: int) -> ReportOut:
    """Screen 4h's data source (F8.1-F8.2): read-only in V2, gains
    escalation resolution in V-023 (this module's own
    `resolve_escalation_for_run`) and flag annotation/override in V-026.
    A run must have finished (`done`) before there's anything real to
    show — never a partial or guessed report."""
    check_run = await _owned_check_run(session, check_run_id, instructor_id)
    if check_run.status != CheckRunStatus.done:
        raise ConflictError("This check hasn't finished yet — its report isn't ready.")

    manuscript = await session.get(Manuscript, check_run.manuscript_id)
    rubric = await session.get(Rubric, check_run.rubric_id)
    report = await session.scalar(
        select(ReadinessReport).where(ReadinessReport.check_run_id == check_run_id)
    )

    rows = (
        await session.execute(
            select(CheckResult, Criterion)
            .join(Criterion, Criterion.id == CheckResult.criterion_id)
            .where(CheckResult.check_run_id == check_run_id)
            .order_by(Criterion.position)
        )
    ).all()
    results = [_to_criterion_result(result, criterion) for result, criterion in rows]

    scoring_payload = await build_report_payload(session, check_run_id)
    return ReportOut(
        check_run_id=check_run_id,
        manuscript_group_label=manuscript.group_label,
        rubric_title=rubric.title,
        status=report.status.value if report is not None else scoring_payload["status"],
        composite_score=(
            float(report.composite_score)
            if report is not None and report.composite_score is not None
            else None
        ),
        thresholds=scoring_payload["thresholds"],
        reason=scoring_payload["reason"],
        results=results,
    )


def _to_escalated_out(item: EscalatedItem) -> EscalatedItemOut:
    return EscalatedItemOut(
        check_result_id=item.check_result_id,
        criterion_id=item.criterion_id,
        criterion_text=item.criterion_text,
        weight=item.weight,
        agreement=item.agreement,
        votes=item.votes,
        ai_majority_verdict=item.detail.get("verdict"),
        reason=item.reason,
    )


async def list_escalated_for_run(
    session: AsyncSession, check_run_id: int, instructor_id: int
) -> list[EscalatedItemOut]:
    """The escalated panel's data source (V-023, screen 4h, "AI wasn't
    sure — review these")."""
    await _owned_check_run(session, check_run_id, instructor_id)
    items = await list_escalated(session, check_run_id)
    return [_to_escalated_out(item) for item in items]


async def resolve_escalation_for_run(
    session: AsyncSession,
    check_run_id: int,
    check_result_id: int,
    instructor_id: int,
    resolution: str,
    reason: str,
) -> ResolveEscalationOut:
    """Resolves one escalated criterion, then recomputes the composite
    score/status LIVE (ticket AC: "resolution updates score + status
    live") — the response carries the fresh `ReportOut` so the frontend
    never has to guess whether a follow-up fetch is needed."""
    await _owned_check_run(session, check_run_id, instructor_id)
    result = await resolve_escalation(
        session, check_run_id, check_result_id, instructor_id, resolution, reason
    )
    await aggregate_and_score(session, check_run_id)
    report = await get_report(session, check_run_id, instructor_id)
    return ResolveEscalationOut(
        check_result_id=result.id,
        outcome=result.outcome.value,
        score=float(result.score) if result.score is not None else None,
        report=report,
    )
