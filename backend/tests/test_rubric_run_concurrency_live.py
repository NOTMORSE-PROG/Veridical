"""F2.4: the first run and a criterion edit must agree on one committed history."""

import asyncio
import os
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.db import sqlalchemy_url
from app.errors import ConflictError, NotFoundError
from app.models.enums import CriterionType, IngestStatus
from app.models.group import Program
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckRun
from app.pipeline import service as pipeline
from app.rubric import service as rubrics
from app.rubric.schemas import CriterionIn, UpdateCriteriaRequest

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="integration: needs a separate PostgreSQL server"
)
SCRATCH_DB = "veridical_rubric_run_concurrency_test"


@pytest.fixture(scope="module")
def scratch_url():
    from alembic import command
    from tests.test_schema import _admin_execute, _alembic_config, _swap_db

    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    url = _swap_db(base, SCRATCH_DB)
    command.upgrade(_alembic_config(url), "head")
    yield url
    asyncio.run(_admin_execute(base, f'DROP DATABASE "{SCRATCH_DB}" WITH (FORCE)'))


@pytest.fixture
async def sessions(scratch_url):
    engine = create_async_engine(sqlalchemy_url(scratch_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            text(
                "TRUNCATE check_run, criterion, rubric, manuscript, instructor "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield factory
    await engine.dispose()


async def seed(sessions):
    async with sessions() as session:
        owner = Instructor(
            email=f"history-{uuid.uuid4().hex}@example.test", display_name="History control"
        )
        session.add(owner)
        await session.flush()
        rubric = Rubric(instructor_id=owner.id, title="History control", is_active=True)
        manuscript = Manuscript(
            instructor_id=owner.id,
            file_ref="unused-synthetic-source.pdf",
            group_label="Synthetic history control",
            ingest_status=IngestStatus.done,
        )
        session.add_all([rubric, manuscript])
        await session.flush()
        criterion = Criterion(
            rubric_id=rubric.id,
            type=CriterionType.structural,
            text="Original requirement",
            weight=1,
            position=0,
        )
        session.add(criterion)
        await session.commit()
        return owner.id, rubric.id, manuscript.id, criterion.id


def edit_body(criterion_id, *, confirm=False):
    return UpdateCriteriaRequest(
        criteria=[
            CriterionIn(id=criterion_id, type="structural", text="Edited requirement", weight=1)
        ],
        confirm=confirm,
    )


async def blocked_before_completion(sessions, pid, task):
    async with asyncio.timeout(5):
        async with sessions() as observer:
            while not task.done():
                if await observer.scalar(select(func.pg_blocking_pids(pid))):
                    return True
                await asyncio.sleep(0.01)
    return False


async def stored_history(sessions):
    async with sessions() as session:
        return (
            list((await session.scalars(select(Criterion.text))).all()),
            list((await session.scalars(select(CheckRun.rubric_id))).all()),
        )


@pytest.mark.parametrize("confirm", [False, True])
async def test_edit_first_keeps_first_run_waiting_until_criteria_commit(
    sessions, monkeypatch, confirm
):
    owner, rubric_id, manuscript_id, criterion_id = await seed(sessions)
    edit_has_guard, release_edit = asyncio.Event(), asyncio.Event()
    original_guard = rubrics._has_reports
    tasks = []
    async with sessions() as editor, sessions() as creator:

        async def hold_editor_guard(session, requested_id):
            result = await original_guard(session, requested_id)
            if session is editor:
                edit_has_guard.set()
                await release_edit.wait()
            return result

        monkeypatch.setattr(rubrics, "_has_reports", hold_editor_guard)
        creator_pid = await creator.scalar(select(func.pg_backend_pid()))
        editing = asyncio.create_task(
            rubrics.update_criteria(
                editor, rubric_id, edit_body(criterion_id, confirm=confirm), owner
            )
        )
        tasks.append(editing)
        try:
            await asyncio.wait_for(edit_has_guard.wait(), 5)
            creating = asyncio.create_task(
                pipeline.create_check_run(
                    creator,
                    owner,
                    manuscript_id,
                    rubric_id,
                    Settings(_env_file=None, veridical_fake_llm=True),
                )
            )
            tasks.append(creating)
            blocked = await blocked_before_completion(sessions, creator_pid, creating)
            history_before_edit_commit = await stored_history(sessions)
            release_edit.set()
            await asyncio.wait_for(asyncio.gather(editing, creating), 5)
            history_after_edit_commit = await stored_history(sessions)
            assert blocked, (
                "First run committed while admitted edit was paused; "
                f"before={history_before_edit_commit!r}, after={history_after_edit_commit!r}"
            )
            assert history_after_edit_commit == (["Edited requirement"], [rubric_id])
        finally:
            release_edit.set()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize("flush_before_pause", [False, True])
async def test_first_run_commit_makes_waiting_editor_refuse_without_changing_history(
    sessions, monkeypatch, confirm, flush_before_pause
):
    owner, rubric_id, manuscript_id, criterion_id = await seed(sessions)
    run_flushed, release_creation = asyncio.Event(), asyncio.Event()
    tasks = []
    async with sessions() as creator, sessions() as editor:
        original_commit = creator.commit

        async def hold_creation_commit():
            if flush_before_pause:
                await creator.flush()
            run_flushed.set()
            await release_creation.wait()
            await original_commit()

        monkeypatch.setattr(creator, "commit", hold_creation_commit)
        editor_pid = await editor.scalar(select(func.pg_backend_pid()))
        creating = asyncio.create_task(
            pipeline.create_check_run(
                creator,
                owner,
                manuscript_id,
                rubric_id,
                Settings(_env_file=None, veridical_fake_llm=True),
            )
        )
        tasks.append(creating)
        try:
            await asyncio.wait_for(run_flushed.wait(), 5)
            editing = asyncio.create_task(
                rubrics.update_criteria(
                    editor, rubric_id, edit_body(criterion_id, confirm=confirm), owner
                )
            )
            tasks.append(editing)
            blocked = await blocked_before_completion(sessions, editor_pid, editing)
            release_creation.set()
            created, edited = await asyncio.wait_for(
                asyncio.gather(creating, editing, return_exceptions=True), 5
            )
            assert not isinstance(created, BaseException), created
            assert blocked and isinstance(edited, ConflictError), (
                "Edit passed an uncommitted first-run guard; "
                f"blocked={blocked!r}, edit={edited!r}, stored={await stored_history(sessions)!r}"
            )
            assert await stored_history(sessions) == (["Original requirement"], [rubric_id])
        finally:
            release_creation.set()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_check_creation_revalidates_cached_active_rubric(sessions):
    owner, rubric_id, manuscript_id, _criterion_id = await seed(sessions)
    async with sessions() as creator:
        cached = await creator.get(Rubric, rubric_id)
        assert cached.is_active
        async with sessions() as changer:
            current = await changer.get(Rubric, rubric_id)
            current.is_active = False
            await changer.commit()
        with pytest.raises(ConflictError, match="confirmed, active rubric"):
            await pipeline.create_check_run(
                creator,
                owner,
                manuscript_id,
                rubric_id,
                Settings(_env_file=None, veridical_fake_llm=True),
            )
        await creator.rollback()
    assert await stored_history(sessions) == (["Original requirement"], [])


async def test_waiting_editor_refreshes_cached_criteria_membership(sessions):
    owner, rubric_id, _, criterion_id = await seed(sessions)
    async with sessions() as editor, sessions() as changer:
        cached = await rubrics.get_rubric(editor, rubric_id, owner)
        assert cached.criteria[0].id == criterion_id
        await rubrics.get_rubric(changer, rubric_id, owner, for_update=True)
        old = await changer.get(Criterion, criterion_id)
        await changer.delete(old)
        replacement = Criterion(
            rubric_id=rubric_id,
            type=CriterionType.structural,
            text="Replacement requirement",
            weight=1,
            position=0,
        )
        changer.add(replacement)
        await changer.flush()
        editor_pid = await editor.scalar(select(func.pg_backend_pid()))
        editing = asyncio.create_task(
            rubrics.update_criteria(editor, rubric_id, edit_body(criterion_id), owner)
        )
        try:
            assert await blocked_before_completion(sessions, editor_pid, editing)
            await changer.commit()
            with pytest.raises(NotFoundError, match="do not belong"):
                await asyncio.wait_for(editing, 5)
            await editor.rollback()
            assert await stored_history(sessions) == (["Replacement requirement"], [])
        finally:
            await changer.rollback()
            await asyncio.gather(editing, return_exceptions=True)


async def test_new_version_refreshes_cached_program_after_family_wait(
    sessions, monkeypatch, tmp_path
):
    owner, rubric_id, _, _ = await seed(sessions)
    async with sessions() as session:
        program = Program(name=f"Concurrency-{uuid.uuid4().hex[:12]}")
        session.add(program)
        await session.commit()
        program_id = program.id
    held, release = asyncio.Event(), asyncio.Event()
    observed = []

    class StopBeforeSourceIO(Exception):
        pass

    async def observe_committed_version(*_args):
        async with sessions() as observer:
            observed.extend(
                (
                    await observer.execute(
                        select(Rubric.program_id, Rubric.version).where(Rubric.version == 2)
                    )
                ).all()
            )
        raise StopBeforeSourceIO

    async def chunks():
        raise AssertionError("No source or provider work belongs in this control")
        yield b""

    monkeypatch.setattr(rubrics, "save_upload", observe_committed_version)
    async with sessions() as uploader, sessions() as changer:
        cached = await uploader.get(Rubric, rubric_id)
        assert cached.program_id is None
        family_id = cached.rubric_family_id
        original_commit = changer.commit

        async def hold_commit():
            await changer.flush()
            held.set()
            await release.wait()
            await original_commit()

        monkeypatch.setattr(changer, "commit", hold_commit)
        uploader_pid = await uploader.scalar(select(func.pg_backend_pid()))
        changing = asyncio.create_task(
            rubrics.set_rubric_family_program(changer, family_id, program_id, owner)
        )
        tasks = [changing]
        try:
            await asyncio.wait_for(held.wait(), 5)
            uploading = asyncio.create_task(
                rubrics.create_rubric_from_upload(
                    uploader,
                    chunks(),
                    "not-read.docx",
                    "New version",
                    None,
                    Settings(_env_file=None, data_dir=tmp_path, veridical_fake_llm=True),
                    instructor_id=owner,
                    family_id=family_id,
                )
            )
            tasks.append(uploading)
            assert await blocked_before_completion(sessions, uploader_pid, uploading)
            release.set()
            await asyncio.wait_for(changing, 5)
            with pytest.raises(StopBeforeSourceIO):
                await asyncio.wait_for(uploading, 5)
            assert observed == [(program_id, 2)]
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("mutation", ["program", "activate"])
@pytest.mark.parametrize("editor_first", [False, True])
async def test_family_writers_use_one_order_with_confirmation(
    sessions, monkeypatch, mutation, editor_first
):
    owner, latest_id, _, criterion_id = await seed(sessions)
    async with sessions() as session:
        latest = await session.get(Rubric, latest_id)
        latest.version = 2
        older = Rubric(
            instructor_id=owner,
            rubric_family_id=latest.rubric_family_id,
            version=1,
            title="Earlier version",
            is_active=False,
        )
        session.add(older)
        await session.commit()
        older_id, family_id = older.id, latest.rubric_family_id
    held, release = asyncio.Event(), asyncio.Event()
    original_lock = rubrics._lock_rubric_family
    tasks = []
    async with sessions() as editor, sessions() as writer:
        first_session = editor if editor_first else writer

        async def pause_after_family_lock(session, requested_family, instructor):
            await original_lock(session, requested_family, instructor)
            if session is first_session:
                held.set()
                await release.wait()

        monkeypatch.setattr(rubrics, "_lock_rubric_family", pause_after_family_lock)
        other_pid = await (writer if editor_first else editor).scalar(select(func.pg_backend_pid()))

        async def edit():
            return await rubrics.update_criteria(
                editor, latest_id, edit_body(criterion_id, confirm=True), owner
            )

        async def mutate():
            if mutation == "program":
                return await rubrics.set_rubric_family_program(writer, family_id, None, owner)
            return await rubrics.activate_rubric(writer, older_id, owner)

        try:
            first = asyncio.create_task(edit() if editor_first else mutate())
            tasks.append(first)
            await asyncio.wait_for(held.wait(), 5)
            second = asyncio.create_task(mutate() if editor_first else edit())
            tasks.append(second)
            assert await blocked_before_completion(sessions, other_pid, second)
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
            async with sessions() as session:
                active = list(
                    (await session.scalars(select(Rubric.id).where(Rubric.is_active))).all()
                )
                assert active == (
                    [older_id] if editor_first and mutation == "activate" else [latest_id]
                )
                assert await session.scalar(select(Criterion.text)) == "Edited requirement"
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_an_unrelated_rubric_check_does_not_wait_for_edit(sessions, monkeypatch):
    owner, rubric_id, _manuscript_id, criterion_id = await seed(sessions)
    other_owner, other_rubric, other_manuscript, _ = await seed(sessions)
    edit_has_guard, release_edit = asyncio.Event(), asyncio.Event()
    original_guard = rubrics._has_reports
    async with sessions() as editor, sessions() as creator:

        async def hold_editor_guard(session, requested_id):
            result = await original_guard(session, requested_id)
            if session is editor:
                edit_has_guard.set()
                await release_edit.wait()
            return result

        monkeypatch.setattr(rubrics, "_has_reports", hold_editor_guard)
        editing = asyncio.create_task(
            rubrics.update_criteria(editor, rubric_id, edit_body(criterion_id), owner)
        )
        try:
            await asyncio.wait_for(edit_has_guard.wait(), 5)
            created = await asyncio.wait_for(
                pipeline.create_check_run(
                    creator,
                    other_owner,
                    other_manuscript,
                    other_rubric,
                    Settings(_env_file=None, veridical_fake_llm=True),
                ),
                5,
            )
            assert created.rubric_id == other_rubric
        finally:
            release_edit.set()
            await editing


async def test_cancelled_first_run_releases_edit_gate_without_history(sessions, monkeypatch):
    owner, rubric_id, manuscript_id, criterion_id = await seed(sessions)
    run_flushed, release_creation = asyncio.Event(), asyncio.Event()
    tasks = []

    async def create_then_pause():
        async with sessions() as creator:
            original_commit = creator.commit

            async def hold_commit():
                await creator.flush()
                run_flushed.set()
                await release_creation.wait()
                await original_commit()

            monkeypatch.setattr(creator, "commit", hold_commit)
            return await pipeline.create_check_run(
                creator,
                owner,
                manuscript_id,
                rubric_id,
                Settings(_env_file=None, veridical_fake_llm=True),
            )

    async with sessions() as editor:
        editor_pid = await editor.scalar(select(func.pg_backend_pid()))
        creating = asyncio.create_task(create_then_pause())
        tasks.append(creating)
        try:
            await asyncio.wait_for(run_flushed.wait(), 5)
            editing = asyncio.create_task(
                rubrics.update_criteria(editor, rubric_id, edit_body(criterion_id), owner)
            )
            tasks.append(editing)
            blocked = await blocked_before_completion(sessions, editor_pid, editing)
            creating.cancel()
            with pytest.raises(asyncio.CancelledError):
                await creating
            await asyncio.wait_for(editing, 5)
            assert blocked, "Edit completed while first-run transaction still owned history"
            assert await stored_history(sessions) == (["Edited requirement"], [])
        finally:
            release_creation.set()
            if not creating.done():
                creating.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
