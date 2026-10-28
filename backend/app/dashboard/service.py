"""Dashboard KPI aggregation (screen 4e). Read-only, pure aggregation
queries over what V-018/V-019 already persist — no new state.
"""

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.escalation import NEEDS_REVIEW_OUTCOMES
from app.config import Settings, get_settings
from app.dashboard.schemas import DashboardStats
from app.models.enums import (
    CheckRunStatus,
    IngestStatus,
    ManuscriptOrigin,
    ReadinessStatus,
    ResultOutcome,
)
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
    # its latest completed assessment. Actionable queues below additionally
    # require the absolute latest run to be done.
    latest_done_run_ids_select = (
        select(func.max(CheckRun.id).label("run_id"))
        .select_from(CheckRun)
        .join(Manuscript, Manuscript.id == CheckRun.manuscript_id)
        .where(Manuscript.instructor_id == instructor_id, CheckRun.status == CheckRunStatus.done)
        .group_by(CheckRun.manuscript_id)
    )
    latest_done_run_ids = latest_done_run_ids_select.cte("latest_done_runs")

    # Aggregate each population independently: joining reports to criterion
    # rows before counting would multiply reports with multiple criteria.
    report_counts = (
        select(
            *(
                func.count().filter(ReadinessReport.status == status).label(f"{status.value}_count")
                for status in ReadinessStatus
            ),
            func.count().filter(ReadinessReport.decision.is_not(None)).label("decided_count"),
        )
        .where(ReadinessReport.check_run_id.in_(select(latest_done_run_ids.c.run_id)))
        .subquery("report_counts")
    )

    # Only rubric-criterion results count toward escalation rate/awaiting-
    # review — integrity checks (F4-F7, criterion_id NULL) don't exist in
    # V2 and aren't part of this ratio's meaning either way. Scoped to the
    # same latest-done-run set as everything else above (BUG-012).
    criterion_counts = (
        select(
            func.count().label("total_criterion_results"),
            func.count().filter(CheckResult.outcome == ResultOutcome.escalated).label("escalated"),
            func.count()
            .filter(CheckResult.outcome.in_(NEEDS_REVIEW_OUTCOMES))
            .label("escalations_awaiting_review"),
        )
        .where(
            CheckResult.check_run_id.in_(select(latest_done_run_ids.c.run_id)),
            CheckResult.criterion_id.is_not(None),
        )
        .subquery("criterion_counts")
    )

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
        .order_by(CheckRun.id.desc())
        .limit(1)
        .correlate(Manuscript)
        .scalar_subquery()
    )
    latest_done_run_id = (
        select(CheckRun.id)
        .where(CheckRun.manuscript_id == Manuscript.id, CheckRun.status == CheckRunStatus.done)
        .order_by(CheckRun.id.desc())
        .limit(1)
        .correlate(Manuscript)
        .scalar_subquery()
    )
    per_manuscript_escalation_count = (
        select(func.count(CheckResult.id))
        .where(
            CheckResult.check_run_id == latest_done_run_id,
            CheckResult.criterion_id.is_not(None),
            CheckResult.outcome.in_(NEEDS_REVIEW_OUTCOMES),
        )
        .correlate(Manuscript)
        .scalar_subquery()
    )
    manuscript_scope = (
        Manuscript.instructor_id == instructor_id,
        Manuscript.dismissed_at.is_(None),
        # V-077 (D-028): Library submissions are not check inputs.
        Manuscript.origin == ManuscriptOrigin.check_upload,
    )

    # BUG-211/349: the same current-run predicate as the Review Desk. An older
    # completed report remains readable during a rerun but is not current work.
    has_decision = (
        select(ReadinessReport.id)
        .where(
            ReadinessReport.check_run_id == latest_done_run_id,
            ReadinessReport.decision.is_not(None),
        )
        .correlate(Manuscript)
        .exists()
    )
    queue_counts = (
        select(
            func.count()
            .filter(
                and_(
                    latest_run_status == CheckRunStatus.done,
                    ~has_decision,
                    per_manuscript_escalation_count == 0,
                )
            )
            .label("ready_to_decide_count"),
            func.count()
            .filter(and_(latest_run_status == CheckRunStatus.done, has_decision))
            .label("complete_queue_count"),
            func.count()
            .filter(
                or_(
                    and_(
                        latest_run_status == CheckRunStatus.done,
                        ~has_decision,
                        per_manuscript_escalation_count > 0,
                    ),
                    latest_run_status.in_((CheckRunStatus.failed, CheckRunStatus.cancelled)),
                    and_(
                        latest_run_status.is_(None),
                        Manuscript.ingest_status == IngestStatus.failed,
                    ),
                ),
            )
            .label("needs_attention_count"),
            func.count()
            .filter(
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
            .label("checking_count"),
            func.count()
            .filter(
                and_(
                    Manuscript.ingest_status == IngestStatus.done,
                    latest_run_status.is_(None),
                )
            )
            .label("not_checked_count"),
        )
        .select_from(Manuscript)
        .where(*manuscript_scope)
        .subquery("queue_counts")
    )

    # BUG-216 / F8: one statement gives every aggregate the same PostgreSQL
    # snapshot. Authentication may already have read through this session;
    # preserve that transaction rather than changing isolation or committing it.
    counts = (
        (
            await session.execute(
                select(
                    select(func.count())
                    .select_from(latest_done_run_ids)
                    .scalar_subquery()
                    .label("manuscripts_checked"),
                    *report_counts.c,
                    *criterion_counts.c,
                    *queue_counts.c,
                )
                .select_from(report_counts)
                .join(criterion_counts, true())
                .join(queue_counts, true())
            )
        )
        .mappings()
        .one()
    )
    total_criterion_results = counts["total_criterion_results"]
    escalation_rate = (
        counts["escalated"] / total_criterion_results if total_criterion_results else None
    )

    return DashboardStats(
        manuscripts_checked=counts["manuscripts_checked"],
        ready_count=counts["ready_count"],
        conditionally_ready_count=counts["conditionally_ready_count"],
        not_ready_count=counts["not_ready_count"],
        needs_review_count=counts["needs_review_count"],
        escalations_awaiting_review=counts["escalations_awaiting_review"],
        escalation_rate=escalation_rate,
        escalation_budget=settings.escalation_budget,
        system_underperforming=(
            escalation_rate is not None and escalation_rate > settings.escalation_budget
        ),
        decided_count=counts["decided_count"],
        complete_queue_count=counts["complete_queue_count"],
        ready_to_decide_count=counts["ready_to_decide_count"],
        needs_attention_count=counts["needs_attention_count"],
        checking_count=counts["checking_count"],
        not_checked_count=counts["not_checked_count"],
    )
