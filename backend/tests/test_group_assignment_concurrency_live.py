"""V-081 (F1): assignment identity under real PostgreSQL concurrency."""

import asyncio
import os

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import sqlalchemy_url
from app.errors import ConflictError, NotFoundError
from app.ingest.service import confirm_manuscript_group, get_manuscript_group
from app.models.enums import ManuscriptOrigin
from app.models.group import Group
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="needs PostgreSQL")
SCRATCH_DB = "veridical_assignmentconcurrencytest"


@pytest.fixture(scope="module")
def scratch_url():
    from alembic import command
    from tests.test_schema import _admin_execute, _alembic_config, _swap_db

    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    url = _swap_db(base, SCRATCH_DB)
    command.upgrade(_alembic_config(url), "head")
    yield url
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))


@pytest.fixture
async def setup(scratch_url):
    engine = create_async_engine(sqlalchemy_url(scratch_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(text("TRUNCATE instructor RESTART IDENTITY CASCADE"))
            owner = Instructor(email="assignment@example.test", display_name="Assignment control")
            other = Instructor(email="other@example.test", display_name="Other control")
            session.add_all([owner, other])
            await session.flush()
            group = Group(instructor_id=owner.id, name="Initial", name_normalized="initial")
            session.add(group)
            await session.flush()
            manuscript = Manuscript(
                instructor_id=owner.id,
                group_id=group.id,
                group_label=group.name,
                file_ref="unused.pdf",
                origin=ManuscriptOrigin.check_upload,
            )
            session.add(manuscript)
            await session.commit()
            ids = owner.id, other.id, manuscript.id, group.id
        yield factory, ids
    finally:
        await engine.dispose()


async def test_same_memberless_confirmation_race_creates_once_and_replays(setup):
    factory, (owner, _, manuscript_id, initial_id) = setup

    async def submit():
        async with factory() as session:
            group, outcome = await confirm_manuscript_group(
                session,
                manuscript_id,
                owner,
                "Concurrent memberless",
                [],
                None,
                expected_group_id=initial_id,
                expected_provided=True,
            )
            return group.id, outcome

    results = await asyncio.gather(*(submit() for _ in range(6)))
    assert len({group_id for group_id, _ in results}) == 1
    assert [outcome for _, outcome in results].count("created") == 1
    assert [outcome for _, outcome in results].count("unchanged") == 5
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Group)) == 2
        saved = await get_manuscript_group(session, manuscript_id, owner)
        assert saved.confirmation == "recorded"
        assert saved.group.id == results[0][0]


async def test_different_concurrent_requests_with_same_expected_assignment_conflict(setup):
    factory, (owner, _, manuscript_id, initial_id) = setup

    async def submit(name):
        async with factory() as session:
            try:
                return await confirm_manuscript_group(
                    session,
                    manuscript_id,
                    owner,
                    name,
                    [],
                    None,
                    expected_group_id=initial_id,
                    expected_provided=True,
                )
            except ConflictError:
                return "conflict"

    results = await asyncio.gather(submit("Choice A"), submit("Choice B"))
    assert results.count("conflict") == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Group)) == 2


async def test_current_group_foreign_manuscript_origin_and_corrupt_fk_are_not_disclosed(setup):
    factory, (owner, other, manuscript_id, _) = setup
    async with factory() as session:
        with pytest.raises(NotFoundError):
            await get_manuscript_group(session, manuscript_id, other)
        with pytest.raises(NotFoundError):
            await confirm_manuscript_group(session, manuscript_id, other, "Attack", [], None)
        foreign_group = Group(
            instructor_id=other, name="Private foreign", name_normalized="private foreign"
        )
        session.add(foreign_group)
        await session.flush()
        manuscript = await session.get(Manuscript, manuscript_id)
        manuscript.group_id = foreign_group.id
        await session.commit()
    async with factory() as session:
        with pytest.raises(NotFoundError):
            await get_manuscript_group(session, manuscript_id, owner)
        with pytest.raises(NotFoundError):
            await confirm_manuscript_group(session, manuscript_id, owner, "Attack", [], None)
        manuscript = await session.get(Manuscript, manuscript_id)
        manuscript.origin = ManuscriptOrigin.library_submission
        await session.commit()
    async with factory() as session:
        with pytest.raises(NotFoundError):
            await get_manuscript_group(session, manuscript_id, owner)
        with pytest.raises(NotFoundError):
            await confirm_manuscript_group(session, manuscript_id, owner, "Attack", [], None)
