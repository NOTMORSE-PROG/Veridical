"""V-021 live-DB tests: `list_manuscripts`'s pagination (ticket edge case:
100+ manuscripts in defense season, server pagination from day one) and
the latest-check-run join used by the dashboard table's row actions.
"""

import os

import pytest
from sqlalchemy import text

from app.ingest.service import list_manuscripts
from app.models.enums import CheckRunStatus, ReadinessStatus
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun, ReadinessReport

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
                "TRUNCATE readiness_report, check_run, rubric, manuscript, instructor "
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
