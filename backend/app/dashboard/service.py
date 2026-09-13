"""Dashboard KPI aggregation (screen 4e). Read-only, pure aggregation
queries over what V-018/V-019 already persist — no new state.
"""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.dashboard.schemas import DashboardStats
from app.models.enums import CheckRunStatus, IngestStatus, ReadinessStatus, ResultOutcome
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

    # V-038: how many of these are actually decided (F8.5), not just
    # scored -- same latest-done-run scope as every other count here.
    decided_count = (
        await session.scalar(
            select(func.count())
            .select_from(ReadinessReport)
            .where(
                ReadinessReport.check_run_id.in_(select(latest_done_run_ids.c.run_id)),
                ReadinessReport.decision.is_not(None),
            )
        )
    ) or 0

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

    # BUG-211: mirrors `list_manuscripts(status=checked, needs_review=False)`
    # exactly (latest run done, no decision, zero unresolved escalations on
    # THAT manuscript's own latest run) -- not derivable from the band
    # counts above, which is what the frontend used to do and got wrong.
    ready_to_decide_count = (
        await session.scalar(
            select(func.count())
            .select_from(latest_done_run_ids)
            .where(
                ~select(ReadinessReport.id)
                .where(
                    ReadinessReport.check_run_id == latest_done_run_ids.c.run_id,
                    ReadinessReport.decision.is_not(None),
                )
                .exists(),
                ~select(CheckResult.id)
                .where(
                    CheckResult.check_run_id == latest_done_run_ids.c.run_id,
                    CheckResult.criterion_id.is_not(None),
                    CheckResult.outcome == ResultOutcome.escalated,
                )
                .exists(),
            )
        )
    ) or 0

    # BUG-212: "Needs you" and "In progress" tab badges need real counts
    # too, mirroring `list_manuscripts`'s own `needs_attention`/`checking`
    # predicates exactly (`ingest/service.py`) -- scoped to ALL of the
    # instructor's non-dismissed manuscripts, not just `latest_done_run_
    # ids` above, since both predicates explicitly include manuscripts
    # whose latest run is NOT done (that's the whole point of "checking",
    # and `needs_attention` also covers failed/cancelled runs and failed
    # ingestion with no run at all).
    latest_run_status = (
        select(CheckRun.status)
        .where(CheckRun.manuscript_id == Manuscript.id)
        .order_by(CheckRun.created_at.desc(), CheckRun.id.desc())
        .limit(1)
        .correlate(Manuscript)
        .scalar_subquery()
    )
    latest_done_run_id = (
        select(CheckRun.id)
        .where(CheckRun.manuscript_id == Manuscript.id, CheckRun.status == CheckRunStatus.done)
        .order_by(CheckRun.created_at.desc(), CheckRun.id.desc())
        .limit(1)
        .correlate(Manuscript)
        .scalar_subquery()
    )
    per_manuscript_escalation_count = (
        select(func.count(CheckResult.id))
        .where(
            CheckResult.check_run_id == latest_done_run_id,
            CheckResult.criterion_id.is_not(None),
            CheckResult.outcome == ResultOutcome.escalated,
        )
        .correlate(Manuscript)
        .scalar_subquery()
    )
    manuscript_scope = (
        Manuscript.instructor_id == instructor_id,
        Manuscript.dismissed_at.is_(None),
    )

    needs_attention_count = (
        await session.scalar(
            select(func.count())
            .select_from(Manuscript)
            .where(
                *manuscript_scope,
                or_(
                    and_(
                        latest_run_status == CheckRunStatus.done,
                        per_manuscript_escalation_count > 0,
                    ),
                    latest_run_status.in_((CheckRunStatus.failed, CheckRunStatus.cancelled)),
                    and_(
                        latest_run_status.is_(None),
                        Manuscript.ingest_status == IngestStatus.failed,
                    ),
                ),
            )
        )
    ) or 0

    checking_count = (
        await session.scalar(
            select(func.count())
            .select_from(Manuscript)
            .where(
                *manuscript_scope,
                latest_run_status.in_(
                    (
                        CheckRunStatus.queued,
                        CheckRunStatus.ingesting,
                        CheckRunStatus.structural,
                        CheckRunStatus.semantic,
                        CheckRunStatus.integrity,
                        CheckRunStatus.aggregating,
                    )
                ),
            )
        )
    ) or 0

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
        decided_count=decided_count,
        ready_to_decide_count=ready_to_decide_count,
        needs_attention_count=needs_attention_count,
        checking_count=checking_count,
    )
