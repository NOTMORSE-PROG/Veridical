"""V-018 live-DB tests: `create_check_run`'s validations (ownership,
ingestion/activation preconditions) and `queue_position`'s FIFO ordering
(ticket AC: "second upload while running -> queued, position shown").
"""

import os
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.errors import ConflictError, NotFoundError
from app.models.enums import IngestStatus
from app.models.group import Group, Program
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.pipeline.service import create_check_run, queue_position

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_pipelineservicetest"


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
                "TRUNCATE check_run, criterion, rubric, manuscript, instructor "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _seed_instructor_manuscript_rubric(
    session_factory, *, ingested=True, active=True, purged=False
):
    async with session_factory() as session:
        instructor = Instructor(
            email=f"svc-{time.time_ns()}@test.local", display_name="Service Test"
        )
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(
            instructor_id=instructor.id,
            group_label="G",
            file_ref="x.pdf",
            ingest_status=IngestStatus.done if ingested else IngestStatus.processing,
            purged_at=datetime.now(UTC) if purged else None,
        )
        rubric = Rubric(
            instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=active
        )
        session.add_all([manuscript, rubric])
        await session.commit()
        return instructor.id, manuscript.id, rubric.id


async def test_create_check_run_succeeds_for_ingested_manuscript_and_active_rubric(
    session_factory,
):
    instructor_id, manuscript_id, rubric_id = await _seed_instructor_manuscript_rubric(
        session_factory
    )
    async with session_factory() as session:
        check_run = await create_check_run(session, instructor_id, manuscript_id, rubric_id)
        assert check_run.id is not None
        assert check_run.status == "queued"


async def test_create_check_run_rejects_unfinished_ingestion(session_factory):
    instructor_id, manuscript_id, rubric_id = await _seed_instructor_manuscript_rubric(
        session_factory, ingested=False
    )
    async with session_factory() as session:
        with pytest.raises(ConflictError):
            await create_check_run(session, instructor_id, manuscript_id, rubric_id)


async def test_create_check_run_rejects_a_purged_manuscript(session_factory):
    """backend-critic finding (V-042, P1, live-reproduced): a purged
    manuscript's stored files are gone; without this gate, a check-run
    would queue, then fail deep in the pipeline with a raw
    FileNotFoundError, and the frontend's generic failure copy would
    tell the instructor to retry an action that can never succeed."""
    instructor_id, manuscript_id, rubric_id = await _seed_instructor_manuscript_rubric(
        session_factory, purged=True
    )
    async with session_factory() as session:
        with pytest.raises(ConflictError, match="purged"):
            await create_check_run(session, instructor_id, manuscript_id, rubric_id)


async def test_create_check_run_rejects_inactive_rubric(session_factory):
    instructor_id, manuscript_id, rubric_id = await _seed_instructor_manuscript_rubric(
        session_factory, active=False
    )
    async with session_factory() as session:
        with pytest.raises(ConflictError):
            await create_check_run(session, instructor_id, manuscript_id, rubric_id)


async def test_create_check_run_rejects_another_instructors_manuscript(session_factory):
    instructor_id, manuscript_id, rubric_id = await _seed_instructor_manuscript_rubric(
        session_factory
    )
    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await create_check_run(session, instructor_id + 999, manuscript_id, rubric_id)


async def _seed_with_programs(
    session_factory, *, manuscript_program: str | None, rubric_program: str | None
):
    """A manuscript (via its group) and an active rubric, each optionally
    assigned a real seeded Program (CS/IT come from migration 0025's own
    seed, not created here)."""
    async with session_factory() as session:
        instructor = Instructor(
            email=f"prog-{time.time_ns()}@test.local", display_name="Program Test"
        )
        session.add(instructor)
        await session.commit()

        manuscript_program_id = None
        if manuscript_program is not None:
            manuscript_program_id = await session.scalar(
                select(Program.id).where(Program.name == manuscript_program)
            )
        group = Group(
            instructor_id=instructor.id,
            name="G",
            name_normalized="g",
            program_id=manuscript_program_id,
        )
        session.add(group)
        await session.commit()

        manuscript = Manuscript(
            instructor_id=instructor.id,
            group_id=group.id,
            group_label="G",
            file_ref="x.pdf",
            ingest_status=IngestStatus.done,
        )
        rubric_program_id = None
        if rubric_program is not None:
            rubric_program_id = await session.scalar(
                select(Program.id).where(Program.name == rubric_program)
            )
        rubric = Rubric(
            instructor_id=instructor.id,
            title="Format",
            source_file="r.pdf",
            is_active=True,
            program_id=rubric_program_id,
        )
        session.add_all([manuscript, rubric])
        await session.commit()
        return instructor.id, manuscript.id, rubric.id


async def test_create_check_run_rejects_a_mismatched_program(session_factory):
    """V-064 AC5: the server-side half -- the ONLY guard bulk re-run
    can't route around (per V-041's lesson: a client-side-only filter
    already missed an equivalent cross-family case once)."""
    instructor_id, manuscript_id, rubric_id = await _seed_with_programs(
        session_factory, manuscript_program="CS", rubric_program="IT"
    )
    async with session_factory() as session:
        with pytest.raises(ConflictError, match="CS.*IT"):
            await create_check_run(session, instructor_id, manuscript_id, rubric_id)


async def test_create_check_run_allows_a_matching_program(session_factory):
    instructor_id, manuscript_id, rubric_id = await _seed_with_programs(
        session_factory, manuscript_program="CS", rubric_program="CS"
    )
    async with session_factory() as session:
        check_run = await create_check_run(session, instructor_id, manuscript_id, rubric_id)
        assert check_run.id is not None


async def test_create_check_run_allows_an_unset_rubric_program_against_any_manuscript(
    session_factory,
):
    """AC3: a rubric with no program set is eligible for everything --
    never a lock-out just because the rubric side hasn't been configured."""
    instructor_id, manuscript_id, rubric_id = await _seed_with_programs(
        session_factory, manuscript_program="CS", rubric_program=None
    )
    async with session_factory() as session:
        check_run = await create_check_run(session, instructor_id, manuscript_id, rubric_id)
        assert check_run.id is not None


async def test_create_check_run_allows_an_unset_manuscript_program_against_any_rubric(
    session_factory,
):
    """AC3: a manuscript with no program set may use any rubric."""
    instructor_id, manuscript_id, rubric_id = await _seed_with_programs(
        session_factory, manuscript_program=None, rubric_program="IT"
    )
    async with session_factory() as session:
        check_run = await create_check_run(session, instructor_id, manuscript_id, rubric_id)
        assert check_run.id is not None


async def test_queue_position_is_fifo_among_active_runs(session_factory):
    instructor_id, manuscript_id, rubric_id = await _seed_instructor_manuscript_rubric(
        session_factory
    )
    async with session_factory() as session:
        first = await create_check_run(session, instructor_id, manuscript_id, rubric_id)
        second = await create_check_run(session, instructor_id, manuscript_id, rubric_id)
        third = await create_check_run(session, instructor_id, manuscript_id, rubric_id)

        assert await queue_position(session, first) == 1
        assert await queue_position(session, second) == 2
        assert await queue_position(session, third) == 3


async def test_queue_position_is_none_once_terminal(session_factory):
    instructor_id, manuscript_id, rubric_id = await _seed_instructor_manuscript_rubric(
        session_factory
    )
    async with session_factory() as session:
        run = await create_check_run(session, instructor_id, manuscript_id, rubric_id)
        run.status = "done"
        await session.commit()
        assert await queue_position(session, run) is None
