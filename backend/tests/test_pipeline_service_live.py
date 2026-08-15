"""V-018 live-DB tests: `create_check_run`'s validations (ownership,
ingestion/activation preconditions) and `queue_position`'s FIFO ordering
(ticket AC: "second upload while running -> queued, position shown").
"""

import os
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.errors import ConflictError, NotFoundError
from app.models.enums import IngestStatus
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
