"""V-021 live-DB tests: `get_dashboard_stats` — KPI counts must match DB
truth (ticket AC), failed/still-running runs must never corrupt the
counts, and the escalation-rate self-reporting (D-012) must be honest
(None when nothing's been graded, never a fabricated 0%).
"""

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.config import get_settings
from app.dashboard.service import get_dashboard_stats
from app.models.enums import (
    CheckKind,
    CheckRunStatus,
    IngestStatus,
    LLMMode,
    ManuscriptOrigin,
    ReadinessStatus,
    ResultOutcome,
)
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


@pytest.mark.parametrize("outcome", [ResultOutcome.passed, ResultOutcome.escalated])
async def test_bug216_stats_do_not_mix_a_concurrent_run_completion(
    session_factory, monkeypatch, outcome
):
    """Commit after the first stats read, rather than hoping to hit a race."""
    async with session_factory() as setup:
        owner = Instructor(email="snapshot@demo.local", display_name="Snapshot")
        setup.add(owner)
        await setup.commit()
        run = await _make_run(setup, owner, run_status=CheckRunStatus.aggregating)
        criterion = Criterion(
            rubric_id=run.rubric_id,
            type="structural",
            text="Document requirement",
            weight=Decimal("10"),
            position=0,
        )
        setup.add(criterion)
        await setup.commit()
        owner_id, run_id, criterion_id = owner.id, run.id, criterion.id

    async with session_factory() as reader:
        # Authentication has already read through this request transaction.
        assert await reader.scalar(select(Instructor.id).where(Instructor.id == owner_id))
        original_scalar, original_execute = reader.scalar, reader.execute
        committed = False

        async def commit_completion_after_read(result):
            nonlocal committed
            if not committed:
                async with session_factory() as writer:
                    current = await writer.get(CheckRun, run_id)
                    current.status = CheckRunStatus.done
                    writer.add(
                        ReadinessReport(
                            check_run_id=run_id,
                            status=(
                                ReadinessStatus.ready
                                if outcome == ResultOutcome.passed
                                else ReadinessStatus.needs_review
                            ),
                        )
                    )
                    writer.add(
                        CheckResult(
                            check_run_id=run_id,
                            criterion_id=criterion_id,
                            kind=CheckKind.structural,
                            outcome=outcome,
                        )
                    )
                    await writer.commit()
                committed = True
            return result

        async def scalar(*args, **kwargs):
            return await commit_completion_after_read(await original_scalar(*args, **kwargs))

        async def execute(*args, **kwargs):
            return await commit_completion_after_read(await original_execute(*args, **kwargs))

        with monkeypatch.context() as patch:
            patch.setattr(reader, "scalar", scalar)
            patch.setattr(reader, "execute", execute)
            stats = await get_dashboard_stats(reader, owner_id)
        assert committed, "The competing transaction must really commit during the request"
        assert stats.manuscripts_checked == 0
        assert (
            stats.ready_count
            + stats.conditionally_ready_count
            + stats.not_ready_count
            + stats.needs_review_count
        ) == 0
        assert stats.checking_count == 1
        assert stats.ready_to_decide_count == stats.needs_attention_count == 0
        assert stats.escalations_awaiting_review == 0
        assert stats.escalation_rate is None

    # A later request must observe the committed completion without stale cache.
    async with session_factory() as reader:
        stats = await get_dashboard_stats(reader, owner_id)
        assert stats.manuscripts_checked == 1
        assert stats.checking_count == 0
        assert (
            stats.ready_count == stats.ready_to_decide_count == int(outcome == ResultOutcome.passed)
        )
        assert (
            stats.needs_review_count
            == stats.needs_attention_count
            == int(outcome == ResultOutcome.escalated)
        )
        assert stats.escalations_awaiting_review == int(outcome == ResultOutcome.escalated)


async def test_bug216_stats_preserve_the_callers_open_transaction(session_factory):
    async with session_factory() as reader:
        owner = Instructor(email="caller@demo.local", display_name="Caller")
        reader.add(owner)
        await reader.commit()
        # A pending caller write must remain visible here and uncommitted elsewhere.
        pending = Manuscript(
            instructor_id=owner.id,
            group_label="Pending",
            file_ref="pending.docx",
            ingest_status=IngestStatus.done,
        )
        reader.add(pending)
        await reader.flush()
        transaction = reader.get_transaction()
        stats = await get_dashboard_stats(reader, owner.id)
        assert stats.not_checked_count == 1
        assert reader.get_transaction() is transaction
        async with session_factory() as observer:
            assert await observer.get(Manuscript, pending.id) is None
        await reader.rollback()


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


@pytest.mark.parametrize("ungraded", ["quota_exhausted", "api_down"])
async def test_ungraded_criteria_are_pending_work_not_ready_to_decide(session_factory, ungraded):
    async with session_factory() as session:
        owner = Instructor(email="ungraded-owner@demo.local", display_name="Owner")
        other = Instructor(email="ungraded-other@demo.local", display_name="Other")
        session.add_all([owner, other])
        await session.commit()
        await _make_run(
            session,
            owner,
            run_status=CheckRunStatus.done,
            report_status="needs_review",
            results=[ungraded, ungraded],
        )
        clean = await _make_run(
            session,
            owner,
            run_status=CheckRunStatus.done,
            report_status="ready",
            results=["passed"],
        )
        await _make_run(
            session,
            other,
            run_status=CheckRunStatus.done,
            report_status="needs_review",
            results=[ungraded],
        )
        session.add(
            CheckResult(
                check_run_id=clean.id,
                criterion_id=None,
                kind=CheckKind.internal_agreement,
                outcome=ResultOutcome(ungraded),
                detail={"reason": "Unavailable integrity check"},
            )
        )
        await session.commit()

        stats = await get_dashboard_stats(session, owner.id)
        assert stats.ready_to_decide_count == 1
        assert stats.needs_attention_count == 1
        assert stats.escalations_awaiting_review == 2
        # A provider failure is pending work, not a low-confidence model vote.
        assert stats.escalation_rate == 0


@pytest.mark.parametrize("ungraded", ["quota_exhausted", "api_down"])
async def test_ungraded_queue_filters_counts_and_latest_resolution_agree(session_factory, ungraded):
    from app.ingest.schemas import ManuscriptQueueStatus
    from app.ingest.service import list_manuscripts

    async with session_factory() as session:
        owner = Instructor(email="ungraded-queue@demo.local", display_name="Owner")
        session.add(owner)
        await session.commit()
        pending = await _make_run(
            session,
            owner,
            run_status=CheckRunStatus.done,
            report_status="needs_review",
            results=[ungraded, ungraded],
        )
        clean = await _make_run(
            session,
            owner,
            run_status=CheckRunStatus.done,
            report_status="ready",
            results=["passed"],
        )
        attention = await list_manuscripts(
            session,
            owner.id,
            status=ManuscriptQueueStatus.needs_attention,
        )
        assert attention.total == 1
        assert attention.items[0].id == pending.manuscript_id
        assert attention.items[0].escalations_awaiting_review == 2
        review = await list_manuscripts(session, owner.id, needs_review=True)
        assert [row.id for row in review.items] == [pending.manuscript_id]
        ready = await list_manuscripts(
            session,
            owner.id,
            status=ManuscriptQueueStatus.checked,
            needs_review=False,
        )
        assert [row.id for row in ready.items] == [clean.manuscript_id]

        # The old ungraded run must not keep a successfully rechecked manuscript
        # in the pending queue; historical results remain unchanged and readable.
        await _make_run(
            session,
            owner,
            manuscript_id=pending.manuscript_id,
            run_status=CheckRunStatus.done,
            report_status="ready",
            results=["passed"],
        )
        attention = await list_manuscripts(
            session,
            owner.id,
            status=ManuscriptQueueStatus.needs_attention,
        )
        assert attention.total == 0
        stats = await get_dashboard_stats(session, owner.id)
        assert stats.ready_to_decide_count == 2
        assert stats.needs_attention_count == 0
        assert stats.escalations_awaiting_review == 0


async def test_bug349_transaction_start_time_cannot_resurrect_pending_work(session_factory):
    """Use real transaction defaults, without rewriting any created_at value."""
    from app.ingest.schemas import ManuscriptQueueStatus
    from app.ingest.service import list_manuscripts

    async with session_factory() as setup:
        owner = Instructor(email="transaction-order@demo.local", display_name="Owner")
        setup.add(owner)
        await setup.flush()
        manuscript = Manuscript(instructor_id=owner.id, group_label="Order", file_ref="order.pdf")
        setup.add(manuscript)
        await setup.commit()

    async with session_factory() as early, session_factory() as later:
        early_time = await early.scalar(text("SELECT transaction_timestamp()"))
        older = await _make_run(
            later,
            owner,
            manuscript_id=manuscript.id,
            run_status=CheckRunStatus.done,
            report_status="needs_review",
            results=["quota_exhausted", "quota_exhausted"],
        )
        newer = CheckRun(
            manuscript_id=manuscript.id,
            rubric_id=older.rubric_id,
            status=CheckRunStatus.done,
            llm_mode=LLMMode.fake,
        )
        early.add(newer)
        await early.flush()
        criteria = (
            await early.scalars(select(Criterion.id).where(Criterion.rubric_id == older.rubric_id))
        ).all()
        early.add(ReadinessReport(check_run_id=newer.id, status=ReadinessStatus.ready))
        early.add_all(
            [
                CheckResult(
                    check_run_id=newer.id,
                    criterion_id=criterion_id,
                    kind=CheckKind.structural,
                    outcome=ResultOutcome.passed,
                    score=100,
                )
                for criterion_id in criteria
            ]
        )
        await early.commit()
        assert newer.id > older.id
        assert newer.created_at == early_time < older.created_at
        page = await list_manuscripts(early, owner.id)
        assert page.items[0].latest_check_run_id == newer.id
        assert page.items[0].latest_done_check_run_id == newer.id
        assert page.items[0].escalations_awaiting_review == 0
        assert (
            await list_manuscripts(early, owner.id, status=ManuscriptQueueStatus.needs_attention)
        ).total == 0
        stats = await get_dashboard_stats(early, owner.id)
        assert stats.ready_count == stats.ready_to_decide_count == 1
        assert stats.needs_attention_count == stats.escalations_awaiting_review == 0


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
    """V-038 / UX review finding: the dashboard gave no KPI signal at all
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


async def test_bug211_ready_to_decide_count_excludes_a_manuscript_with_an_unresolved_escalation(
    session_factory,
):
    """BUG-211: the frontend used to derive this count as `manuscripts_
    checked - needs_review_count - decided_count`, silently assuming a
    manuscript's readiness BAND (`needs_review_count`, a ReadinessReport.
    status value) is the same fact as whether it currently has an
    unresolved escalation. It isn't: a `not_ready`-band report can still
    carry an unresolved escalated criterion. `ready_to_decide_count` must
    mirror `list_manuscripts(status=checked, needs_review=False)`'s real
    predicate -- latest run done, zero unresolved escalations, no decision
    -- not the band."""
    async with session_factory() as session:
        instructor = Instructor(email="dash8@demo.local", display_name="Dash Test 8")
        session.add(instructor)
        await session.commit()

        # Genuinely ready to decide: no escalation, no decision yet.
        await _make_run(
            session,
            instructor,
            run_status=CheckRunStatus.done,
            report_status="conditionally_ready",
            results=["passed", "passed"],
        )
        # Already decided -- must not count twice.
        await _make_run(
            session,
            instructor,
            run_status=CheckRunStatus.done,
            report_status="ready",
            results=["passed"],
            decision="approved",
        )
        # The case the old arithmetic got wrong: `not_ready` band (not
        # `needs_review` band), but a real unresolved escalation exists.
        # The old formula would have counted this as ready-to-decide; the
        # real backend queue (`status=checked, needs_review=False`) never
        # would.
        await _make_run(
            session,
            instructor,
            run_status=CheckRunStatus.done,
            report_status="not_ready",
            results=["failed", "escalated"],
        )

        stats = await get_dashboard_stats(session, instructor.id)
        assert stats.manuscripts_checked == 3
        assert stats.decided_count == 1
        assert stats.needs_review_count == 0  # no report actually carries this band
        # The old buggy formula (3 - 0 - 1 = 2) would have failed this.
        assert stats.ready_to_decide_count == 1


async def test_bug212_needs_attention_and_checking_counts_match_the_real_queue_predicates(
    session_factory,
):
    """BUG-212: the dashboard's "Needs you"/"In progress" tab badges had no
    real count backing them at all (unlike `ready_to_decide_count`/
    `decided_count`) -- these two new counts must mirror `list_manuscripts`'s
    own `needs_attention`/`checking` predicates exactly, including the cases
    those predicates cover that `manuscripts_checked` and friends do NOT:
    a failed run, a cancelled run, and a failed ingestion with no run at
    all are all "needs attention" despite having no done latest run."""
    from app.models.enums import IngestStatus

    async with session_factory() as session:
        instructor = Instructor(email="dash9@demo.local", display_name="Dash Test 9")
        session.add(instructor)
        await session.commit()

        # needs_attention: a done run with an unresolved escalation.
        await _make_run(
            session,
            instructor,
            run_status=CheckRunStatus.done,
            report_status="not_ready",
            results=["failed", "escalated"],
        )
        # needs_attention: the latest run itself failed.
        await _make_run(session, instructor, run_status=CheckRunStatus.failed)
        # needs_attention: the latest run was cancelled.
        await _make_run(session, instructor, run_status=CheckRunStatus.cancelled)
        # needs_attention: failed ingestion, no check_run at all.
        session.add(
            Manuscript(
                instructor_id=instructor.id,
                group_label="G",
                file_ref="failed.pdf",
                ingest_status=IngestStatus.failed,
            )
        )
        # checking: a run still in progress.
        await _make_run(session, instructor, run_status=CheckRunStatus.semantic)
        # Neither: a clean done run, no escalation, no decision -- this is
        # the ready_to_decide case, not needs_attention or checking.
        await _make_run(
            session,
            instructor,
            run_status=CheckRunStatus.done,
            report_status="ready",
            results=["passed"],
        )
        await session.commit()

        stats = await get_dashboard_stats(session, instructor.id)
        assert stats.needs_attention_count == 4
        assert stats.checking_count == 1


async def test_bug212_a_superseded_run_never_double_counts_or_leaks_into_needs_attention(
    session_factory,
):
    """backend review finding (BUG-212 review): the shipped test only ever
    constructed single-run manuscripts, so it couldn't catch a stale run's
    status/escalations leaking past a newer superseding run -- exactly the
    class of bug BUG-012 named for this whole file. Two manuscripts, each
    with two runs: one where a stale FAILED run is superseded by a clean
    DONE run (must NOT count -- the newer run is what matters), and one
    where a stale DONE-with-escalation run is superseded by a CANCELLED run
    (must count exactly ONCE, via the cancelled latest run, not twice)."""
    async with session_factory() as session:
        instructor = Instructor(email="dash10@demo.local", display_name="Dash Test 10")
        session.add(instructor)
        await session.commit()

        # Manuscript A: stale FAILED run, then a later clean DONE run.
        # The stale failure must not leak forward -- this manuscript is
        # NOT needs_attention.
        manuscript_a = Manuscript(instructor_id=instructor.id, group_label="A", file_ref="a.pdf")
        session.add(manuscript_a)
        await session.commit()
        await _make_run(
            session, instructor, manuscript_id=manuscript_a.id, run_status=CheckRunStatus.failed
        )
        await _make_run(
            session,
            instructor,
            manuscript_id=manuscript_a.id,
            run_status=CheckRunStatus.done,
            report_status="ready",
            results=["passed"],
        )

        # Manuscript B: stale DONE-with-escalation run, then a later
        # CANCELLED run. Must count exactly once (via the cancelled latest
        # run), never twice, and never via the stale escalation.
        manuscript_b = Manuscript(instructor_id=instructor.id, group_label="B", file_ref="b.pdf")
        session.add(manuscript_b)
        await session.commit()
        await _make_run(
            session,
            instructor,
            manuscript_id=manuscript_b.id,
            run_status=CheckRunStatus.done,
            report_status="not_ready",
            results=["failed", "escalated"],
        )
        await _make_run(
            session, instructor, manuscript_id=manuscript_b.id, run_status=CheckRunStatus.cancelled
        )

        stats = await get_dashboard_stats(session, instructor.id)
        assert stats.needs_attention_count == 1  # only manuscript B, exactly once
        assert stats.checking_count == 0


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


async def test_not_checked_count_matches_owned_visible_check_inputs(session_factory):
    """V-081: count uploaded check inputs, not Library, failed or foreign rows."""
    from app.ingest.service import list_manuscripts

    async with session_factory() as session:
        owner = Instructor(email="desk-owner@demo.local", display_name="Owner")
        other = Instructor(email="desk-other@demo.local", display_name="Other")
        session.add_all([owner, other])
        await session.commit()
        eligible = []
        for label, attributes in [
            ("unchecked A", {}),
            ("unchecked B", {}),
            ("foreign", {"instructor_id": other.id}),
            ("library", {"origin": ManuscriptOrigin.library_submission}),
            ("dismissed", {"dismissed_at": datetime.now(UTC)}),
            ("failed", {"ingest_status": IngestStatus.failed}),
            ("pending", {"ingest_status": IngestStatus.pending}),
            ("processing", {"ingest_status": IngestStatus.processing}),
        ]:
            values = dict(instructor_id=owner.id, ingest_status=IngestStatus.done)
            values.update(attributes)
            manuscript = Manuscript(group_label=label, file_ref="fixture.pdf", **values)
            session.add(manuscript)
            if label.startswith("unchecked"):
                eligible.append(manuscript)
        await session.commit()
        for status in (CheckRunStatus.queued, CheckRunStatus.failed, CheckRunStatus.done):
            manuscript = Manuscript(
                instructor_id=owner.id,
                group_label=status.value,
                file_ref="has-run.pdf",
                ingest_status=IngestStatus.done,
            )
            session.add(manuscript)
            await session.commit()
            await _make_run(session, owner, manuscript_id=manuscript.id, run_status=status)

        stats = await get_dashboard_stats(session, owner.id)
        page = await list_manuscripts(session, owner.id, status="not_checked")
        assert stats.not_checked_count == 2
        assert page.total == stats.not_checked_count
        assert {row.id for row in page.items} == {row.id for row in eligible}
        assert (await get_dashboard_stats(session, other.id)).not_checked_count == 1


@pytest.mark.parametrize("new_status", [s for s in CheckRunStatus if s != CheckRunStatus.done])
async def test_bug430_complete_queue_excludes_newer_unfinished_or_unsuccessful_runs(
    session_factory, new_status
):
    from app.ingest.service import list_manuscripts

    async with session_factory() as session:
        owner = Instructor(email="complete-rerun@demo.local", display_name="Owner")
        session.add(owner)
        await session.commit()
        older = await _make_run(
            session,
            owner,
            run_status=CheckRunStatus.done,
            report_status="ready",
            decision="approved",
        )
        await _make_run(
            session,
            owner,
            manuscript_id=older.manuscript_id,
            run_status=new_status,
        )
        stats = await get_dashboard_stats(session, owner.id)
        page = await list_manuscripts(session, owner.id, status="decided")
        assert stats.decided_count == 1
        assert page.total == 0
        assert stats.complete_queue_count == page.total


async def test_bug430_complete_count_matches_all_unfiltered_pages_and_visible_scope(
    session_factory,
):
    from app.ingest.service import list_manuscripts

    async with session_factory() as session:
        owner = Instructor(email="complete-scope@demo.local", display_name="Owner")
        other = Instructor(email="complete-foreign@demo.local", display_name="Other")
        session.add_all([owner, other])
        await session.commit()
        expected = set()
        for decision in ("approved", "returned", "rejected"):
            run = await _make_run(
                session,
                owner,
                run_status=CheckRunStatus.done,
                report_status="ready",
                decision=decision,
            )
            expected.add(run.manuscript_id)
            await _make_run(
                session,
                owner,
                manuscript_id=run.manuscript_id,
                run_status=CheckRunStatus.done,
                report_status="ready",
                decision=decision,
            )
        for label, attributes, instructor in [
            ("dismissed", {"dismissed_at": datetime.now(UTC)}, owner),
            ("library", {"origin": ManuscriptOrigin.library_submission}, owner),
            ("foreign", {}, other),
        ]:
            manuscript = Manuscript(
                instructor_id=instructor.id,
                group_label=label,
                file_ref="fixture.pdf",
                **attributes,
            )
            session.add(manuscript)
            await session.commit()
            await _make_run(
                session,
                instructor,
                manuscript_id=manuscript.id,
                run_status=CheckRunStatus.done,
                report_status="ready",
                decision="approved",
            )
        undecided = await _make_run(
            session,
            owner,
            run_status=CheckRunStatus.done,
            report_status="ready",
            decision="approved",
        )
        await _make_run(
            session,
            owner,
            manuscript_id=undecided.manuscript_id,
            run_status=CheckRunStatus.done,
            report_status="ready",
        )
        await _make_run(session, owner, run_status=CheckRunStatus.done)
        session.add(Manuscript(instructor_id=owner.id, group_label="No run", file_ref="none.pdf"))
        await session.commit()
        stats = await get_dashboard_stats(session, owner.id)
        assert stats.decided_count == 5  # Historical KPI retains dismissed/Library decisions.
        assert stats.complete_queue_count == len(expected) == 3
        pages = [
            await list_manuscripts(session, owner.id, status="decided", page=page, page_size=2)
            for page in (1, 2, 3)
        ]
        assert [len(page.items) for page in pages] == [2, 1, 0]
        assert all(page.total == stats.complete_queue_count for page in pages)
        assert {row.id for page in pages for row in page.items} == expected
        assert (await get_dashboard_stats(session, other.id)).complete_queue_count == 1


@pytest.mark.parametrize("new_decision", [None, "returned"])
async def test_bug430_complete_count_uses_run_id_despite_transaction_time_inversion(
    session_factory, new_decision
):
    from app.ingest.service import list_manuscripts

    async with session_factory() as setup:
        owner = Instructor(email="complete-order@demo.local", display_name="Owner")
        setup.add(owner)
        await setup.commit()
    async with session_factory() as early, session_factory() as later:
        early_time = await early.scalar(text("SELECT transaction_timestamp()"))
        older = await _make_run(
            later,
            owner,
            run_status=CheckRunStatus.done,
            report_status="ready",
            decision="approved",
        )
        newer = CheckRun(
            manuscript_id=older.manuscript_id,
            rubric_id=older.rubric_id,
            status=CheckRunStatus.done,
            llm_mode=LLMMode.fake,
        )
        early.add(newer)
        await early.flush()
        early.add(
            ReadinessReport(
                check_run_id=newer.id,
                status=ReadinessStatus.ready,
                decision=new_decision,
            )
        )
        await early.commit()
        assert newer.id > older.id
        assert newer.created_at == early_time < older.created_at
        stats = await get_dashboard_stats(early, owner.id)
        page = await list_manuscripts(early, owner.id, status="decided")
        assert stats.complete_queue_count == page.total == int(new_decision is not None)
        assert stats.decided_count == int(new_decision is not None)
        if page.items:
            assert page.items[0].latest_check_run_id == newer.id
