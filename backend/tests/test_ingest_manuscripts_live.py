"""V-021 live-DB tests: `list_manuscripts`'s pagination (ticket edge case:
100+ manuscripts in defense season, server pagination from day one) and
the latest-check-run join used by the dashboard table's row actions.
"""

import asyncio
import os
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.archive.service import list_archive
from app.errors import ConflictError, NotFoundError
from app.groups.service import UNSET_PROGRAM_FILTER
from app.ingest.schemas import ManuscriptQueueStatus, ManuscriptSort
from app.ingest.service import dismiss_failed_manuscript, list_manuscripts
from app.models.audit import AuditLog
from app.models.enums import (
    CheckKind,
    CheckRunStatus,
    CriterionType,
    IngestStatus,
    ReadinessStatus,
    ReportDecision,
    ResultOutcome,
)
from app.models.group import Group, Program
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, ReadinessReport

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_manuscriptlisttest"


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
                "TRUNCATE audit_log, readiness_report, check_run, rubric, manuscript, instructor "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def test_pagination_never_returns_more_than_a_page(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="pg@demo.local", display_name="Pagination Test")
        session.add(instructor)
        await session.commit()
        for i in range(25):
            session.add(
                Manuscript(instructor_id=instructor.id, group_label=f"G-{i}", file_ref="x.pdf")
            )
        await session.commit()

        page1 = await list_manuscripts(session, instructor.id, page=1, page_size=10)
        page2 = await list_manuscripts(session, instructor.id, page=2, page_size=10)
        page3 = await list_manuscripts(session, instructor.id, page=3, page_size=10)

        assert page1.total == 25
        assert len(page1.items) == 10
        assert len(page2.items) == 10
        assert len(page3.items) == 5
        # No overlap between pages.
        ids = (
            {i.id for i in page1.items} | {i.id for i in page2.items} | {i.id for i in page3.items}
        )
        assert len(ids) == 25


async def test_latest_check_run_is_surfaced_per_manuscript(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="latest@demo.local", display_name="Latest Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()

        older = CheckRun(
            manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
        )
        session.add(older)
        await session.commit()
        newer = CheckRun(
            manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.semantic
        )
        session.add(newer)
        await session.commit()

        page = await list_manuscripts(session, instructor.id)
        assert page.items[0].latest_check_run_id == newer.id
        assert page.items[0].latest_check_run_status == "semantic"
        # backend-critic finding on BUG-012: the absolute-latest run
        # (still running) must not hide the older DONE run's valid
        # report -- the two are tracked separately.
        assert page.items[0].latest_done_check_run_id == older.id


@pytest.mark.parametrize(
    ("newer_status", "expected_queue"),
    [
        (CheckRunStatus.semantic, ManuscriptQueueStatus.checking),
        (CheckRunStatus.failed, ManuscriptQueueStatus.check_failed),
        (CheckRunStatus.cancelled, ManuscriptQueueStatus.cancelled),
    ],
)
async def test_ready_to_decide_requires_the_absolute_latest_run_to_be_done(
    session_factory,
    newer_status,
    expected_queue,
):
    """BUG-190: an older readable report cannot make a newer re-run look done."""
    async with session_factory() as session:
        instructor = Instructor(
            email=f"ready-latest-{newer_status.value}@demo.local",
            display_name="Latest Ready Test",
        )
        session.add(instructor)
        await session.flush()
        manuscript = Manuscript(
            instructor_id=instructor.id,
            group_label="Latest Ready Team",
            original_filename="latest-ready.pdf",
            file_ref="latest-ready.pdf",
            ingest_status=IngestStatus.done,
        )
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.flush()
        older_done = CheckRun(
            manuscript_id=manuscript.id,
            rubric_id=rubric.id,
            status=CheckRunStatus.done,
        )
        session.add(older_done)
        await session.flush()
        session.add(
            ReadinessReport(
                check_run_id=older_done.id,
                status=ReadinessStatus.conditionally_ready,
                composite_score=Decimal("70"),
            )
        )
        await session.commit()

        ready_before_rerun = await list_manuscripts(
            session,
            instructor.id,
            status=ManuscriptQueueStatus.checked,
            needs_review=False,
        )
        assert [item.id for item in ready_before_rerun.items] == [manuscript.id]

        newer_run = CheckRun(
            manuscript_id=manuscript.id,
            rubric_id=rubric.id,
            status=newer_status,
        )
        session.add(newer_run)
        await session.commit()

        ready_page = await list_manuscripts(
            session,
            instructor.id,
            status=ManuscriptQueueStatus.checked,
            needs_review=False,
        )
        assert ready_page.total == 0
        assert ready_page.items == []

        current_queue = await list_manuscripts(
            session,
            instructor.id,
            status=expected_queue,
        )
        assert [item.id for item in current_queue.items] == [manuscript.id]
        assert current_queue.items[0].latest_check_run_id == newer_run.id
        assert current_queue.items[0].latest_check_run_status == newer_status.value
        assert current_queue.items[0].latest_done_check_run_id == older_done.id


@pytest.mark.parametrize("older_work", ["decided", "needs_review"])
@pytest.mark.parametrize(
    ("newer_status", "expected_queue"),
    [
        (CheckRunStatus.semantic, ManuscriptQueueStatus.checking),
        (CheckRunStatus.failed, ManuscriptQueueStatus.check_failed),
        (CheckRunStatus.cancelled, ManuscriptQueueStatus.cancelled),
    ],
)
async def test_historical_work_does_not_overlap_a_newer_current_run_queue(
    session_factory,
    older_work,
    newer_status,
    expected_queue,
):
    """BUG-190: Complete/Needs-you describe current work, not old reports."""
    async with session_factory() as session:
        instructor = Instructor(
            email=f"historical-{older_work}-{newer_status.value}@demo.local",
            display_name="Historical Queue Test",
        )
        session.add(instructor)
        await session.flush()
        manuscript = Manuscript(
            instructor_id=instructor.id,
            group_label="Historical Queue Team",
            original_filename="historical.pdf",
            file_ref="historical.pdf",
            ingest_status=IngestStatus.done,
        )
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.flush()
        criterion = Criterion(
            rubric_id=rubric.id,
            type=CriterionType.semantic,
            text="The manuscript explains its research design.",
            evidence="Methodology chapter",
            weight=Decimal("1"),
            position=0,
        )
        session.add(criterion)
        await session.flush()
        older_done = CheckRun(
            manuscript_id=manuscript.id,
            rubric_id=rubric.id,
            status=CheckRunStatus.done,
        )
        session.add(older_done)
        await session.flush()
        if older_work == "decided":
            session.add(
                ReadinessReport(
                    check_run_id=older_done.id,
                    status=ReadinessStatus.ready,
                    composite_score=Decimal("90"),
                    decision=ReportDecision.approved,
                )
            )
        else:
            session.add(
                CheckResult(
                    check_run_id=older_done.id,
                    criterion_id=criterion.id,
                    kind=CheckKind.semantic,
                    outcome=ResultOutcome.escalated,
                )
            )
        await session.commit()
        newer_run = CheckRun(
            manuscript_id=manuscript.id,
            rubric_id=rubric.id,
            status=newer_status,
        )
        session.add(newer_run)
        await session.commit()

        historical_page = (
            await list_manuscripts(
                session,
                instructor.id,
                status=ManuscriptQueueStatus.decided,
            )
            if older_work == "decided"
            else await list_manuscripts(session, instructor.id, needs_review=True)
        )
        assert historical_page.total == 0
        assert historical_page.items == []

        current_queue = await list_manuscripts(
            session,
            instructor.id,
            status=expected_queue,
        )
        assert [item.id for item in current_queue.items] == [manuscript.id]
        assert current_queue.items[0].latest_check_run_id == newer_run.id
        assert current_queue.items[0].latest_check_run_status == newer_status.value
        assert current_queue.items[0].latest_done_check_run_id == older_done.id


async def test_a_failed_rerun_does_not_hide_an_earlier_done_runs_report(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="rerun-failed@demo.local", display_name="Rerun Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()

        older = CheckRun(
            manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
        )
        session.add(older)
        await session.commit()
        newer_failed = CheckRun(
            manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.failed
        )
        session.add(newer_failed)
        await session.commit()

        page = await list_manuscripts(session, instructor.id)
        assert page.items[0].latest_check_run_id == newer_failed.id
        assert page.items[0].latest_check_run_status == "failed"
        assert page.items[0].latest_done_check_run_id == older.id


async def test_latest_run_tie_uses_id_for_filter_and_row_metadata(session_factory):
    """The status filter and row metadata share the same deterministic latest run."""
    async with session_factory() as session:
        instructor = Instructor(email="latest-tie@demo.local", display_name="Tie Test")
        session.add(instructor)
        await session.flush()
        manuscript = Manuscript(
            instructor_id=instructor.id,
            group_label="Tie Team",
            original_filename="tie.pdf",
            file_ref="tie.pdf",
            ingest_status=IngestStatus.done,
        )
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.flush()
        older_done = CheckRun(
            manuscript_id=manuscript.id,
            rubric_id=rubric.id,
            status=CheckRunStatus.done,
        )
        newer_failed = CheckRun(
            manuscript_id=manuscript.id,
            rubric_id=rubric.id,
            status=CheckRunStatus.failed,
        )
        session.add_all([older_done, newer_failed])
        await session.commit()
        assert older_done.created_at == newer_failed.created_at
        assert older_done.id < newer_failed.id

        page = await list_manuscripts(
            session,
            instructor.id,
            status=ManuscriptQueueStatus.check_failed,
        )
        assert [item.id for item in page.items] == [manuscript.id]
        assert page.items[0].latest_check_run_id == newer_failed.id
        assert page.items[0].latest_check_run_status == CheckRunStatus.failed.value


async def test_manuscript_with_no_check_run_has_null_latest_fields(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="none@demo.local", display_name="None Test")
        session.add(instructor)
        await session.commit()
        session.add(Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf"))
        await session.commit()

        page = await list_manuscripts(session, instructor.id)
        assert page.items[0].latest_check_run_id is None
        assert page.items[0].latest_check_run_status is None
        assert page.items[0].latest_done_check_run_id is None


async def test_latest_decision_is_surfaced_from_the_latest_done_runs_report(session_factory):
    """V-038 / ux-critic finding: without this, the dashboard gave no
    signal at all that a manuscript had already been decided."""
    async with session_factory() as session:
        instructor = Instructor(email="decided@demo.local", display_name="Decided Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()

        run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done)
        session.add(run)
        await session.commit()
        session.add(
            ReadinessReport(
                check_run_id=run.id,
                status=ReadinessStatus.ready,
                composite_score=90,
                decision="approved",
            )
        )
        await session.commit()

        page = await list_manuscripts(session, instructor.id)
        assert page.items[0].latest_decision == "approved"
        assert page.items[0].latest_readiness == "ready"


async def test_latest_done_rubric_family_id_is_surfaced_and_distinguishes_families(session_factory):
    """V-041 / ux-critic finding (P1, live-reproduced against real
    multi-family seeded data): without this field, a bulk re-run UI has
    no signal to exclude a manuscript whose latest done run was under a
    completely unrelated rubric family."""
    async with session_factory() as session:
        instructor = Instructor(email="family@demo.local", display_name="Family Test")
        session.add(instructor)
        await session.commit()
        cs_format = Rubric(instructor_id=instructor.id, title="CS Format", source_file="cs.pdf")
        it_format = Rubric(instructor_id=instructor.id, title="IT Format", source_file="it.pdf")
        session.add_all([cs_format, it_format])
        await session.commit()

        checked_under_cs = Manuscript(
            instructor_id=instructor.id, group_label="G1", file_ref="x.pdf"
        )
        checked_under_it = Manuscript(
            instructor_id=instructor.id, group_label="G2", file_ref="y.pdf"
        )
        session.add_all([checked_under_cs, checked_under_it])
        await session.commit()

        session.add_all(
            [
                CheckRun(
                    manuscript_id=checked_under_cs.id,
                    rubric_id=cs_format.id,
                    status=CheckRunStatus.done,
                ),
                CheckRun(
                    manuscript_id=checked_under_it.id,
                    rubric_id=it_format.id,
                    status=CheckRunStatus.done,
                ),
            ]
        )
        await session.commit()

        page = await list_manuscripts(session, instructor.id)
        by_id = {item.id: item.latest_done_rubric_family_id for item in page.items}
        assert by_id[checked_under_cs.id] == str(cs_format.rubric_family_id)
        assert by_id[checked_under_it.id] == str(it_format.rubric_family_id)
        assert by_id[checked_under_cs.id] != by_id[checked_under_it.id]


async def test_undecided_report_has_a_null_latest_decision_not_a_fabricated_one(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="undecided@demo.local", display_name="Undecided Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()

        run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done)
        session.add(run)
        await session.commit()
        session.add(
            ReadinessReport(check_run_id=run.id, status=ReadinessStatus.ready, composite_score=90)
        )
        await session.commit()

        page = await list_manuscripts(session, instructor.id)
        assert page.items[0].latest_decision is None


async def test_escalations_awaiting_review_is_surfaced_per_manuscript(session_factory):
    """V-071 (AC1): `newcomer`'s baseline walkthrough had to open reports
    one at a time to find which manuscript held the dashboard's escalation
    count -- this field lets the row say so directly."""
    async with session_factory() as session:
        instructor = Instructor(email="escalated@demo.local", display_name="Escalated Test")
        session.add(instructor)
        await session.commit()
        has_escalations = Manuscript(
            instructor_id=instructor.id, group_label="G1", file_ref="x.pdf"
        )
        clean = Manuscript(instructor_id=instructor.id, group_label="G2", file_ref="y.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([has_escalations, clean, rubric])
        await session.commit()
        criterion = Criterion(
            rubric_id=rubric.id,
            type=CriterionType.semantic,
            text="The methodology states the research design.",
            evidence="Methodology chapter",
            weight=Decimal("1"),
            position=0,
        )
        session.add(criterion)
        await session.commit()

        run_a = CheckRun(
            manuscript_id=has_escalations.id, rubric_id=rubric.id, status=CheckRunStatus.done
        )
        run_b = CheckRun(manuscript_id=clean.id, rubric_id=rubric.id, status=CheckRunStatus.done)
        session.add_all([run_a, run_b])
        await session.commit()
        session.add_all(
            [
                CheckResult(
                    check_run_id=run_a.id,
                    criterion_id=criterion.id,
                    kind=CheckKind.semantic,
                    outcome=ResultOutcome.escalated,
                ),
                CheckResult(
                    check_run_id=run_a.id,
                    criterion_id=criterion.id,
                    kind=CheckKind.semantic,
                    outcome=ResultOutcome.escalated,
                ),
                CheckResult(
                    check_run_id=run_a.id,
                    criterion_id=criterion.id,
                    kind=CheckKind.semantic,
                    outcome=ResultOutcome.passed,
                ),
                CheckResult(
                    check_run_id=run_b.id,
                    criterion_id=criterion.id,
                    kind=CheckKind.semantic,
                    outcome=ResultOutcome.passed,
                ),
            ]
        )
        await session.commit()

        page = await list_manuscripts(session, instructor.id)
        by_id = {item.id: item.escalations_awaiting_review for item in page.items}
        assert by_id[has_escalations.id] == 2
        assert by_id[clean.id] == 0


async def test_review_desk_filters_are_applied_before_pagination(session_factory):
    """V-071 AC2: the Review Desk query is one server-side contract.

    A matching row that would fall beyond page one in the unfiltered list
    must still be returned on page one after filtering; client-side filtering
    of an already paginated slice would incorrectly show an empty queue.
    """
    async with session_factory() as session:
        instructor = Instructor(email="queue@demo.local", display_name="Queue Test")
        session.add(instructor)
        await session.flush()
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add(rubric)
        await session.commit()
        criterion = Criterion(
            rubric_id=rubric.id,
            type=CriterionType.semantic,
            text="The manuscript states its research design.",
            evidence="Methodology chapter",
            weight=Decimal("1"),
            position=0,
        )
        session.add(criterion)
        await session.commit()

        rows = [
            Manuscript(
                instructor_id=instructor.id,
                group_label="Newest Team",
                original_filename="newest.pdf",
                file_ref="newest.pdf",
                ingest_status=IngestStatus.done,
            ),
            Manuscript(
                instructor_id=instructor.id,
                group_label="Review Team",
                original_filename="methodology-review.pdf",
                file_ref="review.pdf",
                ingest_status=IngestStatus.done,
            ),
            Manuscript(
                instructor_id=instructor.id,
                group_label="Failed Upload",
                original_filename="failed.pdf",
                file_ref="failed.pdf",
                ingest_status=IngestStatus.failed,
            ),
        ]
        session.add_all(rows)
        await session.commit()

        review_run = CheckRun(
            manuscript_id=rows[1].id,
            rubric_id=rubric.id,
            status=CheckRunStatus.done,
        )
        session.add(review_run)
        await session.commit()
        session.add(
            CheckResult(
                check_run_id=review_run.id,
                criterion_id=criterion.id,
                kind=CheckKind.semantic,
                outcome=ResultOutcome.escalated,
            )
        )
        await session.commit()

        page = await list_manuscripts(
            session,
            instructor.id,
            q="methodology",
            needs_review=True,
            page=1,
            page_size=1,
        )
        assert page.total == 1
        assert [item.id for item in page.items] == [rows[1].id]

        failed = await list_manuscripts(
            session,
            instructor.id,
            status=ManuscriptQueueStatus.ingestion_failed,
        )
        assert [item.id for item in failed.items] == [rows[2].id]

        exact_group = await list_manuscripts(session, instructor.id, group="review team")
        assert [item.id for item in exact_group.items] == [rows[1].id]


async def test_review_desk_statuses_and_sort_share_latest_run_semantics(session_factory):
    """V-071 AC2: queue filters, row metadata, and sorting use the same
    definitions of latest run, latest DONE report, and criterion task."""
    async with session_factory() as session:
        instructor = Instructor(email="statuses@demo.local", display_name="Status Test")
        session.add(instructor)
        await session.commit()
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add(rubric)
        await session.commit()
        criterion = Criterion(
            rubric_id=rubric.id,
            type=CriterionType.semantic,
            text="The manuscript states its research design.",
            evidence="Methodology chapter",
            weight=Decimal("1"),
            position=0,
        )
        session.add(criterion)
        await session.commit()

        labels = ["Not checked", "Needs review", "Decided", "Failed", "Cancelled", "Checking"]
        manuscripts = [
            Manuscript(
                instructor_id=instructor.id,
                group_label=label,
                original_filename=f"{label.casefold().replace(' ', '-')}.pdf",
                file_ref="x.pdf",
                ingest_status=IngestStatus.done,
            )
            for label in labels
        ]
        session.add_all(manuscripts)
        ingestion_failed = Manuscript(
            instructor_id=instructor.id,
            group_label="Ingestion failed",
            original_filename="ingestion-failed.pdf",
            file_ref="failed.pdf",
            ingest_status=IngestStatus.failed,
        )
        session.add(ingestion_failed)
        await session.commit()

        review_run = CheckRun(
            manuscript_id=manuscripts[1].id,
            rubric_id=rubric.id,
            status=CheckRunStatus.done,
        )
        decided_run = CheckRun(
            manuscript_id=manuscripts[2].id,
            rubric_id=rubric.id,
            status=CheckRunStatus.done,
        )
        failed_run = CheckRun(
            manuscript_id=manuscripts[3].id,
            rubric_id=rubric.id,
            status=CheckRunStatus.failed,
        )
        cancelled_run = CheckRun(
            manuscript_id=manuscripts[4].id,
            rubric_id=rubric.id,
            status=CheckRunStatus.cancelled,
        )
        checking_run = CheckRun(
            manuscript_id=manuscripts[5].id,
            rubric_id=rubric.id,
            status=CheckRunStatus.semantic,
        )
        session.add_all([review_run, decided_run, failed_run, cancelled_run, checking_run])
        await session.commit()
        session.add_all(
            [
                CheckResult(
                    check_run_id=review_run.id,
                    criterion_id=criterion.id,
                    kind=CheckKind.semantic,
                    outcome=ResultOutcome.escalated,
                ),
                ReadinessReport(
                    check_run_id=decided_run.id,
                    status=ReadinessStatus.ready,
                    composite_score=Decimal("90"),
                    decision=ReportDecision.approved,
                ),
            ]
        )
        await session.commit()

        expected = {
            ManuscriptQueueStatus.needs_attention: {
                manuscripts[1].id,
                manuscripts[3].id,
                manuscripts[4].id,
                ingestion_failed.id,
            },
            ManuscriptQueueStatus.ingestion_failed: {ingestion_failed.id},
            ManuscriptQueueStatus.not_checked: {manuscripts[0].id},
            ManuscriptQueueStatus.checking: {manuscripts[5].id},
            ManuscriptQueueStatus.check_failed: {manuscripts[3].id},
            ManuscriptQueueStatus.cancelled: {manuscripts[4].id},
            ManuscriptQueueStatus.checked: {manuscripts[1].id},
            ManuscriptQueueStatus.decided: {manuscripts[2].id},
        }
        for status, ids in expected.items():
            page = await list_manuscripts(session, instructor.id, status=status)
            assert {item.id for item in page.items} == ids

        sorted_page = await list_manuscripts(
            session,
            instructor.id,
            sort=ManuscriptSort.needs_review_desc,
        )
        assert sorted_page.items[0].id == manuscripts[1].id
        assert sorted_page.items[0].escalations_awaiting_review == 1


async def test_failed_upload_dismissal_retains_archive_and_audit_history(session_factory):
    """V-071 AC4: dismiss removes active-desk clutter without deleting or
    reclassifying the failed record; repeat requests are idempotent."""
    async with session_factory() as session:
        instructor = Instructor(email="dismiss@demo.local", display_name="Dismiss Test")
        other = Instructor(email="other@demo.local", display_name="Other Test")
        session.add_all([instructor, other])
        await session.commit()
        failed = Manuscript(
            instructor_id=instructor.id,
            group_label="Failed Team",
            original_filename="broken.pdf",
            file_ref="broken.pdf",
            ingest_status=IngestStatus.failed,
        )
        healthy = Manuscript(
            instructor_id=instructor.id,
            group_label="Healthy Team",
            original_filename="healthy.pdf",
            file_ref="healthy.pdf",
            ingest_status=IngestStatus.done,
        )
        other_failed = Manuscript(
            instructor_id=other.id,
            group_label="Other Team",
            original_filename="other.pdf",
            file_ref="other.pdf",
            ingest_status=IngestStatus.failed,
        )
        session.add_all([failed, healthy, other_failed])
        await session.commit()

        before = await list_manuscripts(session, instructor.id)
        assert failed.id in {item.id for item in before.items}

        dismissed = await dismiss_failed_manuscript(session, instructor.id, failed.id)
        first_timestamp = dismissed.dismissed_at
        assert first_timestamp is not None

        active = await list_manuscripts(session, instructor.id)
        assert failed.id not in {item.id for item in active.items}

        archive = await list_archive(session, instructor.id)
        archived = next(item for item in archive.items if item.manuscript_id == failed.id)
        assert archived.ingest_status == IngestStatus.failed
        assert archived.dismissed_at == first_timestamp

        repeated = await dismiss_failed_manuscript(session, instructor.id, failed.id)
        assert repeated.dismissed_at == first_timestamp
        event_count = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.event_type == "manuscript_ingestion_failure_dismissed",
                AuditLog.payload["manuscript_id"].as_integer() == failed.id,
            )
        )
        assert event_count == 1

        with pytest.raises(ConflictError):
            await dismiss_failed_manuscript(session, instructor.id, healthy.id)
        with pytest.raises(NotFoundError):
            await dismiss_failed_manuscript(session, instructor.id, other_failed.id)


async def test_concurrent_failed_upload_dismissal_writes_one_audit_event(session_factory):
    """Two dismissals serialize into one retained state and one immutable event."""
    async with session_factory() as setup:
        instructor = Instructor(email="dismiss-race@demo.local", display_name="Dismiss Race")
        setup.add(instructor)
        await setup.flush()
        failed = Manuscript(
            instructor_id=instructor.id,
            group_label="Failed Race Team",
            original_filename="race-broken.pdf",
            file_ref="race-broken.pdf",
            ingest_status=IngestStatus.failed,
        )
        setup.add(failed)
        await setup.commit()
        instructor_id = instructor.id
        manuscript_id = failed.id

    async def dismiss_once():
        async with session_factory() as session:
            dismissed = await dismiss_failed_manuscript(session, instructor_id, manuscript_id)
            return dismissed.dismissed_at

    first, second = await asyncio.gather(dismiss_once(), dismiss_once())
    assert first == second

    async with session_factory() as verify:
        event_count = await verify.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.event_type == "manuscript_ingestion_failure_dismissed",
                AuditLog.manuscript_id == manuscript_id,
            )
        )
        assert event_count == 1


async def test_program_filter_and_display_are_sourced_through_the_group(session_factory):
    """V-062 AC5: `program` filters GET /manuscripts to only manuscripts
    whose group has that program set, and every row's `program` field
    reflects its OWN group's program, never a different manuscript's."""
    async with session_factory() as session:
        instructor = Instructor(email="program@demo.local", display_name="Program Test")
        session.add(instructor)
        await session.commit()

        cs = await session.scalar(select(Program).where(Program.name == "CS"))
        cs_group = Group(
            instructor_id=instructor.id, name="CS Team", name_normalized="cs team", program_id=cs.id
        )
        unset_group = Group(
            instructor_id=instructor.id, name="No Program Yet", name_normalized="no program yet"
        )
        session.add_all([cs_group, unset_group])
        await session.commit()

        cs_manuscript = Manuscript(
            instructor_id=instructor.id,
            group_id=cs_group.id,
            group_label=cs_group.name,
            file_ref="a.pdf",
        )
        unset_manuscript = Manuscript(
            instructor_id=instructor.id,
            group_id=unset_group.id,
            group_label=unset_group.name,
            file_ref="b.pdf",
        )
        session.add_all([cs_manuscript, unset_manuscript])
        await session.commit()

        filtered = await list_manuscripts(session, instructor.id, program="CS")
        assert {item.id for item in filtered.items} == {cs_manuscript.id}

        everything = await list_manuscripts(session, instructor.id)
        program_by_id = {item.id: item.program for item in everything.items}
        assert program_by_id[cs_manuscript.id] == "CS"
        assert program_by_id[unset_manuscript.id] is None

        # `ui-designer` finding while speccing the dashboard filter: an
        # inner join alone can only ever match manuscripts that HAVE a
        # program, so "Not set" needs its own reachable filter value.
        unset_only = await list_manuscripts(session, instructor.id, program=UNSET_PROGRAM_FILTER)
        assert {item.id for item in unset_only.items} == {unset_manuscript.id}
