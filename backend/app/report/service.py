"""Aggregation service (F8.1): projects a check_run's persisted
check_results/flags into `ScorableResult`/`ScorableFlag`, runs the pure
`score_check_run`, and persists the composite/status onto the run's
`ReadinessReport` (created once, updated on re-aggregation — e.g. after
an instructor resolves an escalation).
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_event
from app.checks.escalation import (
    NEEDS_REVIEW_OUTCOMES,
    EscalatedItem,
    list_escalated,
    resolve_escalation,
)
from app.config import Settings, get_settings
from app.errors import ConflictError, NotFoundError
from app.models.enums import (
    CheckKind,
    CheckRunStatus,
    FlagSeverity,
    ReadinessStatus,
    ReportDecision,
    RubricParseStatus,
)
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, Flag, ReadinessReport
from app.report.schemas import (
    CriterionResultOut,
    EscalatedItemOut,
    EvidenceItem,
    FlagSummaryOut,
    ReportExportData,
    ReportOut,
    ResolutionOut,
    ResolveEscalationOut,
)
from app.report.scoring import (
    ScorableFlag,
    ScorableResult,
    score_check_run,
    scoring_result_as_dict,
)
from app.report.weight_importance import WeightImportance, weight_importance


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


def _to_criterion_result(
    result: CheckResult,
    criterion: Criterion,
    *,
    importance: WeightImportance,
) -> CriterionResultOut:
    detail = result.detail or {}
    evidence = [EvidenceItem(**item) for item in detail.get("evidence", [])]
    resolution_detail = detail.get("resolution")
    resolution = (
        ResolutionOut(
            type=resolution_detail["type"],
            reason=resolution_detail["reason"],
            ai_majority_verdict=resolution_detail.get("ai_majority_verdict"),
        )
        if resolution_detail
        else None
    )
    return CriterionResultOut(
        criterion_id=criterion.id,
        text=criterion.text,
        type=criterion.type.value,
        weight=float(criterion.weight),
        weight_importance=importance,
        kind=result.kind.value,
        outcome=result.outcome.value,
        score=float(result.score) if result.score is not None else None,
        basis=detail.get("basis"),
        anchor=detail.get("anchor"),
        reasoning=detail.get("reasoning"),
        reason=detail.get("reason"),
        evidence=evidence,
        resolution=resolution,
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
    return await report_out_for_check_run(session, check_run)


async def report_out_for_check_run(
    session: AsyncSession, check_run: CheckRun, settings: Settings | None = None
) -> ReportOut:
    """V-040: the same report-assembly logic `get_report` uses, split out
    so the public share-link view (`app.share.service`, token-authorized
    instead of instructor-owned) can build the identical `ReportOut` a
    signed-in instructor sees -- one source of truth for what a report
    IS, two different authorization paths to reach it."""
    settings = settings or get_settings()
    check_run_id = check_run.id
    if check_run.status != CheckRunStatus.done:
        raise ConflictError("This check hasn't finished yet. Its report isn't ready.")

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
    # D-023: weight is relative (scoring.py normalises, never a
    # must-total-100 rule) -- bucketed into Low/Medium/High relative to
    # this run's own equal-split average, never shown as a bare
    # percentage. `average_weight <= 0` can't happen in practice (every
    # persisted `Criterion.weight` is API-validated `> 0`, schemas.py),
    # but a run with zero criteria could reach this with an empty `rows`.
    average_weight = (
        sum(float(criterion.weight) for _, criterion in rows) / len(rows) if rows else 0.0
    )
    results = [
        _to_criterion_result(
            result,
            criterion,
            importance=weight_importance(
                float(criterion.weight), average_weight=average_weight, settings=settings
            ),
        )
        for result, criterion in rows
    ]

    scoring_payload = await build_report_payload(session, check_run_id)
    pending_review_count = await session.scalar(
        select(func.count())
        .select_from(CheckResult)
        .where(
            CheckResult.check_run_id == check_run_id,
            CheckResult.outcome.in_(NEEDS_REVIEW_OUTCOMES),
        )
    )

    # V-041: the most recent OTHER done+reported run for the SAME
    # manuscript UNDER THE SAME RUBRIC FAMILY (e.g. this run is a
    # re-check under a newer version of the same format) — the
    # version-comparison line's data source. `backend-critic` found live
    # that without the family filter, a manuscript checked against an
    # unrelated format in between two real versions would win the
    # comparison, manufacturing a "v1 -> v2" story that never happened
    # (two different formats' verdicts, not two versions of one). None
    # when this is the manuscript's first reported run under this family.
    previous = (
        await session.execute(
            select(ReadinessReport.status, ReadinessReport.composite_score)
            .join(CheckRun, CheckRun.id == ReadinessReport.check_run_id)
            .join(Rubric, Rubric.id == CheckRun.rubric_id)
            .where(
                CheckRun.manuscript_id == check_run.manuscript_id,
                CheckRun.id != check_run_id,
                CheckRun.status == CheckRunStatus.done,
                CheckRun.created_at < check_run.created_at,
                Rubric.rubric_family_id == rubric.rubric_family_id,
            )
            .order_by(CheckRun.created_at.desc())
            .limit(1)
        )
    ).first()

    return ReportOut(
        check_run_id=check_run_id,
        manuscript_group_label=manuscript.group_label,
        manuscript_original_filename=manuscript.original_filename,
        rubric_title=rubric.title,
        status=report.status.value if report is not None else scoring_payload["status"],
        composite_score=(
            float(report.composite_score)
            if report is not None and report.composite_score is not None
            else None
        ),
        thresholds=scoring_payload["thresholds"],
        reason=scoring_payload["reason"],
        flag_deduction=scoring_payload["flag_deduction"],
        unresolved_high_flag_count=scoring_payload["unresolved_high_flag_count"],
        llm_mode=check_run.llm_mode.value,
        results=results,
        decision=report.decision.value if report is not None and report.decision else None,
        decided_at=report.decided_at if report is not None else None,
        decision_note=report.decision_note if report is not None else None,
        pending_review_count=pending_review_count or 0,
        rubric_is_current=rubric.is_active,
        rubric_needs_review=rubric.parse_status == RubricParseStatus.needs_review,
        rubric_parse_issues=rubric.parse_issues,
        previous_status=previous.status.value if previous is not None else None,
        previous_composite_score=(
            float(previous.composite_score)
            if previous is not None and previous.composite_score is not None
            else None
        ),
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
        review_reason=item.review_reason,
    )


async def list_escalated_for_run(
    session: AsyncSession, check_run_id: int, instructor_id: int
) -> list[EscalatedItemOut]:
    """The escalated panel's data source (V-023, screen 4h, "AI wasn't
    sure — review these")."""
    await _owned_check_run(session, check_run_id, instructor_id)
    items = await list_escalated(session, check_run_id)
    return [_to_escalated_out(item) for item in items]


# BUG-033/`ui-designer` spec: fixed F4→F7 declaration order — flags never
# jump around the panel as severities change, matching this project's own
# recognition-over-recall precedent (Jakob's Law, DESIGN.md).
_CHECK_KIND_ORDER = {
    CheckKind.internal_agreement: 0,
    CheckKind.citation_integrity: 1,
    CheckKind.statistical_forensics: 2,
    CheckKind.originality_reuse: 3,
}
_SEVERITY_ORDER = {FlagSeverity.high: 0, FlagSeverity.med: 1, FlagSeverity.low: 2}


async def list_flags_for_run(
    session: AsyncSession, check_run_id: int, instructor_id: int
) -> list[FlagSummaryOut]:
    """The report's flags panel data source (BUG-033) — its own endpoint,
    not a field on `ReportOut`, mirroring `list_escalated_for_run`'s own
    precedent exactly (a sibling panel self-fetches; the common case of
    zero flags on a run never adds dead weight to every report fetch)."""
    await _owned_check_run(session, check_run_id, instructor_id)
    return await flags_for_check_run(session, check_run_id)


async def flags_for_check_run(session: AsyncSession, check_run_id: int) -> list[FlagSummaryOut]:
    """V-040: split out of `list_flags_for_run` for the same reason as
    `report_out_for_check_run` -- the public share-link view needs the
    identical flags list, reached through token authorization instead of
    instructor ownership."""
    rows = (
        await session.execute(
            select(Flag, CheckResult)
            .join(CheckResult, CheckResult.id == Flag.check_result_id)
            .where(CheckResult.check_run_id == check_run_id)
        )
    ).all()

    criterion_ids = {r.criterion_id for _, r in rows if r.criterion_id is not None}
    criteria_by_id: dict[int, Criterion] = {}
    if criterion_ids:
        criteria = (
            await session.execute(select(Criterion).where(Criterion.id.in_(criterion_ids)))
        ).scalars()
        criteria_by_id = {c.id: c for c in criteria}

    summaries = [
        FlagSummaryOut(
            id=flag.id,
            check_kind=result.kind.value,
            severity=flag.severity.value,
            criterion_text=(
                criteria_by_id[result.criterion_id].text
                if result.criterion_id in criteria_by_id
                else None
            ),
            evidence_excerpt=flag.evidence_excerpt,
            page_anchor=flag.page_anchor,
            overridden=flag.overridden,
        )
        for flag, result in rows
    ]
    return sorted(
        summaries,
        key=lambda f: (
            _CHECK_KIND_ORDER.get(CheckKind(f.check_kind), len(_CHECK_KIND_ORDER)),
            f.overridden,
            _SEVERITY_ORDER.get(FlagSeverity(f.severity), len(_SEVERITY_ORDER)),
            f.id,
        ),
    )


async def get_report_export_data(
    session: AsyncSession, check_run_id: int, instructor_id: int
) -> ReportExportData:
    """V-039 (screen 4h export action): assembles everything the PDF
    export needs from the SAME data sources the on-screen report/flags
    panels already use -- never a second, divergent read path. Ownership
    is validated once, by `get_report` itself (raises `NotFoundError`);
    the two supplementary reads below trust that check.
    """
    report = await get_report(session, check_run_id, instructor_id)
    flags = await list_flags_for_run(session, check_run_id, instructor_id)

    # F7's archive-size disclosure (V-037) lives on its own CheckResult
    # row (criterion_id=None, one per run) -- never surfaced in
    # ReportOut.results (that table is criteria-linked results only), so
    # it needs its own small read here.
    archive_check = await session.scalar(
        select(CheckResult).where(
            CheckResult.check_run_id == check_run_id,
            CheckResult.kind == CheckKind.originality_reuse,
        )
    )
    archive_size_n = (
        archive_check.detail.get("archive_size_n")
        if archive_check is not None and archive_check.detail
        else None
    )

    return ReportExportData(report=report, flags=flags, archive_size_n=archive_size_n)


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
    await raise_if_decided(session, check_run_id)
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


async def _get_owned_report(
    session: AsyncSession, check_run_id: int, instructor_id: int
) -> ReadinessReport:
    """Shared precondition for decide/reopen: the run must be owned, done,
    and have a real persisted report (never a guessed/partial one — the
    same rule `get_report` itself enforces)."""
    check_run = await _owned_check_run(session, check_run_id, instructor_id)
    if check_run.status != CheckRunStatus.done:
        raise ConflictError("This check hasn't finished yet. Its report isn't ready.")
    report = await session.scalar(
        select(ReadinessReport).where(ReadinessReport.check_run_id == check_run_id)
    )
    if report is None:
        raise NotFoundError(f"No report for check run {check_run_id}.")
    return report


async def raise_if_decided(session: AsyncSession, check_run_id: int) -> None:
    """V-038 AC2 ("decided report immutable until reopened") only means
    something if it also binds every OTHER score-mutating action, not just
    a second decide — `backend-critic` found live that overriding a flag
    or resolving an escalation after decision would otherwise silently
    change the persisted composite/status on the SAME `ReadinessReport`
    row a human already signed off on, with no reopen and no audit trace
    of the drift. Called by `resolve_escalation_for_run` (this module) and
    `app.flags.service.override_flag` before either mutates anything —
    both already recompute via `aggregate_and_score` right after, which is
    exactly the write this guards."""
    report = await session.scalar(
        select(ReadinessReport).where(ReadinessReport.check_run_id == check_run_id)
    )
    if report is not None and report.decision is not None:
        raise ConflictError(
            "This report has already been decided. Reopen it first before changing "
            "anything that affects its score."
        )


# BUG-095: every OTHER instructor-written justification on this product
# is required (overriding a flag, resolving an escalation, reopening a
# decision) -- the single highest-stakes, most panel-visible action
# (approving a manuscript for defense) was the one exception, optional
# even when it overrules the system's own computed verdict. Required only
# when the decision DISAGREES with what VERIDICAL computed -- that
# disagreement is exactly what ground rule 1 says the human is for, and
# is the one thing worth capturing. Left optional when decision and
# verdict agree, so a routine approval of an already-Ready manuscript
# isn't forced to invent a reason that doesn't exist.
DECISIONS_REQUIRING_A_REASON: frozenset[tuple[ReportDecision, ReadinessStatus]] = frozenset(
    {
        (ReportDecision.approved, ReadinessStatus.not_ready),
        (ReportDecision.approved, ReadinessStatus.conditionally_ready),
        (ReportDecision.rejected, ReadinessStatus.ready),
    }
)


async def decide_report(
    session: AsyncSession,
    check_run_id: int,
    instructor_id: int,
    decision: str,
    note: str | None,
) -> ReportOut:
    """The terminal gate (V-038, F8.5) — VERIDICAL never sets this itself
    (charter rule 1); this is the ONE place a human decision gets
    recorded. Blocked while any criterion still needs review (the
    instructor must look at what the AI couldn't decide) and while the
    report is already decided (must reopen first, explicitly, with a
    reason — a decision is not silently overwritable)."""
    report = await _get_owned_report(session, check_run_id, instructor_id)
    if report.decision is not None:
        raise ConflictError(
            "This report has already been decided. Reopen it first to change the decision."
        )
    pending = await session.scalar(
        select(func.count())
        .select_from(CheckResult)
        .where(
            CheckResult.check_run_id == check_run_id,
            CheckResult.outcome.in_(NEEDS_REVIEW_OUTCOMES),
        )
    )
    if pending:
        raise ConflictError(
            f"{pending} criteri{'on' if pending == 1 else 'a'} still need your review before "
            "this report can be decided. Resolve them first."
        )
    decision_enum = ReportDecision(decision)
    stripped_note = note.strip() if note and note.strip() else None
    if (decision_enum, report.status) in DECISIONS_REQUIRING_A_REASON and not stripped_note:
        raise ConflictError(
            f"This decision disagrees with VERIDICAL's own {report.status.value.replace('_', ' ')} "
            "verdict. Explain why before confirming, so the audit trail records the disagreement."
        )

    report.decision = decision_enum
    report.decided_at = datetime.now(UTC)
    report.decided_by_instructor_id = instructor_id
    report.decision_note = stripped_note
    # `backend-critic` finding: without a snapshot of what the instructor
    # was actually looking at, a later score drift (a flag override or
    # escalation resolved after this decision — both now blocked by
    # `raise_if_decided`, but the snapshot is what makes any future gap
    # in that guard DETECTABLE from the audit log alone, not just
    # prevented today) would be invisible in the trail.
    await write_audit_event(
        session,
        event_type="report_decided",
        check_run_id=check_run_id,
        payload={
            "instructor_id": instructor_id,
            "decision": decision,
            "note": report.decision_note,
            "composite_score": (
                float(report.composite_score) if report.composite_score is not None else None
            ),
            "status": report.status.value,
        },
    )
    await session.commit()
    return await get_report(session, check_run_id, instructor_id)


async def reopen_report(
    session: AsyncSession,
    check_run_id: int,
    instructor_id: int,
    reason: str,
) -> ReportOut:
    """Undoes a decision so a new one can be made — always explicit, always
    reasoned (AC: "reopen logged with reason"). The prior decision isn't
    erased from history: it's preserved in the immutable audit log
    (`report_decided`, then this `report_reopened` event), even though the
    report's own display fields reset to None (the "current decision"
    fields, not the historical record — see the model's own docstring)."""
    report = await _get_owned_report(session, check_run_id, instructor_id)
    if report.decision is None:
        raise ConflictError("This report hasn't been decided yet. There's nothing to reopen.")

    prior_decision = report.decision.value
    prior_note = report.decision_note
    report.decision = None
    report.decided_at = None
    report.decided_by_instructor_id = None
    report.decision_note = None
    await write_audit_event(
        session,
        event_type="report_reopened",
        check_run_id=check_run_id,
        payload={
            "instructor_id": instructor_id,
            "reason": reason.strip(),
            "prior_decision": prior_decision,
            "prior_note": prior_note,
            "composite_score": (
                float(report.composite_score) if report.composite_score is not None else None
            ),
            "status": report.status.value,
        },
    )
    await session.commit()
    return await get_report(session, check_run_id, instructor_id)
