"""Dashboard KPI aggregation (screen 4e). Read-only, pure aggregation
queries over what V-018/V-019 already persist — no new state.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.dashboard.schemas import DashboardStats
from app.models.enums import CheckRunStatus, ReadinessStatus, ResultOutcome
from app.models.manuscript import Manuscript
from app.models.run import CheckResult, CheckRun, ReadinessReport


async def get_dashboard_stats(
    session: AsyncSession, instructor_id: int, settings: Settings | None = None
) -> DashboardStats:
    settings = settings or get_settings()

    # BUG-012: every count on this dashboard is scoped to ONE manuscript-
    # level unit -- each manuscript's LATEST done CheckRun -- so all four
    # status counts always sum to exactly `manuscripts_checked`, and a
    # superseded re-run's stale escalations don't keep counting once a
    # newer run exists for the same manuscript. A manuscript can have
    # multiple CheckRuns (re-runs); only the newest done one represents
    # its current, actionable state.
    latest_done_run_ids_select = (
        select(func.max(CheckRun.id).label("run_id"))
        .select_from(CheckRun)
        .join(Manuscript, Manuscript.id == CheckRun.manuscript_id)
        .where(Manuscript.instructor_id == instructor_id, CheckRun.status == CheckRunStatus.done)
        .group_by(CheckRun.manuscript_id)
    )
    latest_done_run_ids = latest_done_run_ids_select.subquery()

    manuscripts_checked = await session.scalar(
        select(func.count()).select_from(latest_done_run_ids)
    )

    status_rows = (
        await session.execute(
            select(ReadinessReport.status, func.count())
            .select_from(ReadinessReport)
            .where(ReadinessReport.check_run_id.in_(select(latest_done_run_ids.c.run_id)))
            .group_by(ReadinessReport.status)
        )
    ).all()
    status_counts = {status: count for status, count in status_rows}

    # Only rubric-criterion results count toward escalation rate/awaiting-
    # review — integrity checks (F4-F7, criterion_id NULL) don't exist in
    # V2 and aren't part of this ratio's meaning either way. Scoped to the
    # same latest-done-run set as everything else above (BUG-012).
    criterion_result_rows = (
        await session.execute(
            select(CheckResult.outcome, func.count())
            .select_from(CheckResult)
            .where(
                CheckResult.check_run_id.in_(select(latest_done_run_ids.c.run_id)),
                CheckResult.criterion_id.is_not(None),
            )
            .group_by(CheckResult.outcome)
        )
    ).all()
    outcome_counts = {outcome: count for outcome, count in criterion_result_rows}
    escalated = outcome_counts.get(ResultOutcome.escalated, 0)
    total_criterion_results = sum(outcome_counts.values())
    escalation_rate = escalated / total_criterion_results if total_criterion_results > 0 else None

    return DashboardStats(
        manuscripts_checked=manuscripts_checked or 0,
        ready_count=status_counts.get(ReadinessStatus.ready, 0),
        conditionally_ready_count=status_counts.get(ReadinessStatus.conditionally_ready, 0),
        not_ready_count=status_counts.get(ReadinessStatus.not_ready, 0),
        needs_review_count=status_counts.get(ReadinessStatus.needs_review, 0),
        escalations_awaiting_review=escalated,
        escalation_rate=escalation_rate,
        escalation_budget=settings.escalation_budget,
        system_underperforming=(
            escalation_rate is not None and escalation_rate > settings.escalation_budget
        ),
    )
