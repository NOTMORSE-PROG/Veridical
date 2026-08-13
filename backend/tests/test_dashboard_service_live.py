"""V-021 live-DB tests: `get_dashboard_stats` — KPI counts must match DB
truth (ticket AC), failed/still-running runs must never corrupt the
counts, and the escalation-rate self-reporting (D-012) must be honest
(None when nothing's been graded, never a fabricated 0%).
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.dashboard.service import get_dashboard_stats
from app.models.enums import CheckKind, CheckRunStatus, ReadinessStatus, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, ReadinessReport

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_dashboardtest"


@pytest.fixture(scope="module")
def scratch_url():
    import asyncio

    from alembic import command
    from tests.test_schema import _admin_execute, _alembic_config, _swap_db

    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    url = _swap_db(base, SCRATCH_DB)
    command.upgrade(_alembic_config(url), "head")
    yield url
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))


@pytest.fixture()
def session_factory(scratch_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url

    engine = create_async_engine(sqlalchemy_url(scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean_tables(session_factory):
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE readiness_report, check_result, check_run, criterion, "
                "rubric, manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _make_run(
    session,
    instructor,
    *,
    run_status,
    report_status=None,
    results=(),
    manuscript_id=None,
    decision=None,
):
    if manuscript_id is None:
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        session.add(manuscript)
        await session.commit()
        manuscript_id = manuscript.id
    rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
    session.add(rubric)
    await session.commit()
    criteria = [
        Criterion(
            rubric_id=rubric.id,
            type="structural",
            text=f"C{i}",
            evidence=None,
            weight=Decimal("10"),
            position=i,
        )
        for i in range(len(results))
    ]
    session.add_all(criteria)
    await session.commit()
    for c in criteria:
        await session.refresh(c)

    check_run = CheckRun(manuscript_id=manuscript_id, rubric_id=rubric.id, status=run_status)
    session.add(check_run)
    await session.commit()

    for criterion, outcome in zip(criteria, results, strict=True):
        session.add(
            CheckResult(
                check_run_id=check_run.id,
                criterion_id=criterion.id,
                kind=CheckKind.structural,
                outcome=ResultOutcome(outcome),
                score=100.0 if outcome == "passed" else None,
            )
        )
    if report_status is not None:
        session.add(
            ReadinessReport(
                check_run_id=check_run.id,
                composite_score=None,
                status=ReadinessStatus(report_status),
                decision=decision,
            )
        )
    await session.commit()
    return check_run


async def test_kpis_match_db_truth_across_all_four_statuses(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="dash@demo.local", display_name="Dash Test")
        session.add(instructor)
        await session.commit()

        await _make_run(session, instructor, run_status=CheckRunStatus.done, report_status="ready")
        await _make_run(
            session, instructor, run_status=CheckRunStatus.done, report_status="conditionally_ready"
        )
        await _make_run(
            session, instructor, run_status=CheckRunStatus.done, report_status="not_ready"
        )
        await _make_run(
            session, instructor, run_status=CheckRunStatus.done, report_status="not_ready"
        )
        await _make_run(
            session, instructor, run_status=CheckRunStatus.done, report_status="needs_review"
        )

        stats = await get_dashboard_stats(session, instructor.id)
        assert stats.manuscripts_checked == 5
        assert stats.ready_count == 1
        assert stats.conditionally_ready_count == 1
        assert stats.not_ready_count == 2
        assert stats.needs_review_count == 1


async def test_failed_and_still_running_runs_never_corrupt_the_counts(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="dash2@demo.local", display_name="Dash Test 2")
        session.add(instructor)
        await session.commit()

        await _make_run(session, instructor, run_status=CheckRunStatus.done, report_status="ready")
        await _make_run(session, instructor, run_status=CheckRunStatus.failed)  # no report
        await _make_run(session, instructor, run_status=CheckRunStatus.semantic)  # still running

        stats = await get_dashboard_stats(session, instructor.id)
        # Only the ONE truly-done, reported run counts anywhere.
        assert stats.manuscripts_checked == 1
        assert stats.ready_count == 1
        assert stats.conditionally_ready_count == 0
        assert stats.not_ready_count == 0
        assert stats.needs_review_count == 0


async def test_escalation_rate_is_none_when_nothing_graded_yet(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="dash3@demo.local", display_name="Dash Test 3")
        session.add(instructor)
        await session.commit()
        stats = await get_dashboard_stats(session, instructor.id)
        assert stats.escalation_rate is None
        assert stats.escalations_awaiting_review == 0
        assert stats.system_underperforming is False


async def test_escalation_rate_and_underperforming_banner(session_factory, monkeypatch):
    monkeypatch.setenv("ESCALATION_BUDGET", "0.20")
    get_settings.cache_clear()
    async with session_factory() as session:
        instructor = Instructor(email="dash4@demo.local", display_name="Dash Test 4")
        session.add(instructor)
        await session.commit()
        # 2 escalated out of 4 = 50% > 20% budget.
        await _make_run(
            session,
            instructor,
            run_status=CheckRunStatus.done,
            report_status="not_ready",
            results=["passed", "failed", "escalated", "escalated"],
        )
        stats = await get_dashboard_stats(session, instructor.id)
        assert stats.escalations_awaiting_review == 2
        assert stats.escalation_rate == 0.5
        assert stats.system_underperforming is True


async def test_re_run_only_counts_the_latest_done_run_per_manuscript(session_factory):
    """BUG-012: a manuscript checked twice (a re-run) must only contribute
    ONE unit everywhere -- its newest done run -- so all counts stay
    reconciled and a superseded run's stale escalations don't linger
    forever in "awaiting review"."""
    async with session_factory() as session:
        instructor = Instructor(email="dash5@demo.local", display_name="Dash Test 5")
        session.add(instructor)
        await session.commit()

        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        session.add(manuscript)
        await session.commit()

        # First run: not ready, with an escalated criterion.
        await _make_run(
            session,
            instructor,
            manuscript_id=manuscript.id,
            run_status=CheckRunStatus.done,
            report_status="not_ready",
            results=["failed", "escalated"],
        )
        # Re-run on the SAME manuscript: now ready, nothing escalated.
        await _make_run(
            session,
            instructor,
            manuscript_id=manuscript.id,
            run_status=CheckRunStatus.done,
            report_status="ready",
            results=["passed", "passed"],
        )

        stats = await get_dashboard_stats(session, instructor.id)
        # One manuscript, one unit everywhere -- never double-counted.
        assert stats.manuscripts_checked == 1
        assert stats.ready_count == 1
        assert stats.not_ready_count == 0
        assert (
            stats.ready_count
            + stats.conditionally_ready_count
            + stats.not_ready_count
            + stats.needs_review_count
            == stats.manuscripts_checked
        )
        # The first (superseded) run's escalation must not still count.
        assert stats.escalations_awaiting_review == 0


async def test_decided_count_reflects_v038_decisions_scoped_to_latest_done_run(session_factory):
    """V-038 / ux-critic finding: the dashboard gave no KPI signal at all
    that the terminal decision gate had been used -- this closes that
    gap (AC3, 'reflected on dashboard KPIs')."""
    async with session_factory() as session:
        instructor = Instructor(email="dash6@demo.local", display_name="Dash Test 6")
        session.add(instructor)
        await session.commit()

        await _make_run(
            session,
            instructor,
            run_status=CheckRunStatus.done,
            report_status="ready",
            decision="approved",
        )
        await _make_run(
            session,
            instructor,
            run_status=CheckRunStatus.done,
            report_status="not_ready",
            decision="rejected",
        )
        # Scored but not yet decided -- must not count.
        await _make_run(
            session, instructor, run_status=CheckRunStatus.done, report_status="conditionally_ready"
        )

        stats = await get_dashboard_stats(session, instructor.id)
        assert stats.manuscripts_checked == 3
        assert stats.decided_count == 2


async def test_a_superseded_runs_decision_does_not_linger_in_decided_count(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="dash7@demo.local", display_name="Dash Test 7")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        session.add(manuscript)
        await session.commit()

        # First run: decided approved.
        await _make_run(
            session,
            instructor,
            manuscript_id=manuscript.id,
            run_status=CheckRunStatus.done,
            report_status="ready",
            decision="approved",
        )
        # Re-run on the SAME manuscript: not yet decided.
        await _make_run(
            session,
            instructor,
            manuscript_id=manuscript.id,
            run_status=CheckRunStatus.done,
            report_status="ready",
        )

        stats = await get_dashboard_stats(session, instructor.id)
        assert stats.manuscripts_checked == 1
        assert stats.decided_count == 0
