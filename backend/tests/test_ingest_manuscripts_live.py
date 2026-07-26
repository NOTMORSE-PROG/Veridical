"""V-021 live-DB tests: `list_manuscripts`'s pagination (ticket edge case:
100+ manuscripts in defense season, server pagination from day one) and
the latest-check-run join used by the dashboard table's row actions.
"""

import os

import pytest
from sqlalchemy import text

from app.ingest.service import list_manuscripts
from app.models.enums import CheckRunStatus
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun

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
            text("TRUNCATE check_run, rubric, manuscript, instructor RESTART IDENTITY CASCADE")
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
