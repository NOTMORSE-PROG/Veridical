"""V-024 live-DB tests: instructor scoping (never leak another
instructor's audit rows), filtering (check_run_id, event_type, date
range), pagination, and detail-row shape. DB-level append-only immutability
is already proven by `test_schema.py::test_audit_log_rejects_update_and_delete_at_db_level`
(V-003 grant) — not re-tested here.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.audit.service import get_audit_log_detail, list_audit_log
from app.errors import NotFoundError
from app.models.audit import AuditLog
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_audittest"


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
                "TRUNCATE audit_log, check_run, rubric, manuscript, "
                "instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _make_run(session, instructor):
    manuscript = Manuscript(
        instructor_id=instructor.id,
        group_label="G",
        file_ref="x.pdf",
        original_filename="G-Thesis.pdf",
    )
    rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
    session.add_all([manuscript, rubric])
    await session.commit()
    check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
    session.add(check_run)
    await session.commit()
    return check_run


async def test_scoping_never_leaks_another_instructors_rows(session_factory):
    async with session_factory() as session:
        mine = Instructor(email="mine@test.local", display_name="Mine")
        theirs = Instructor(email="theirs@test.local", display_name="Theirs")
        session.add_all([mine, theirs])
        await session.commit()

        my_run = await _make_run(session, mine)
        their_run = await _make_run(session, theirs)
        session.add(
            AuditLog(
                event_type="llm_call",
                check_run_id=my_run.id,
                payload={"prompt_type": "semantic_grading"},
            )
        )
        session.add(
            AuditLog(
                event_type="llm_call",
                check_run_id=their_run.id,
                payload={"prompt_type": "semantic_grading"},
            )
        )
        await session.commit()

        page = await list_audit_log(session, mine.id)
        assert page.total == 1
        assert page.items[0].check_run_id == my_run.id


async def test_rows_with_no_check_run_id_are_excluded_not_leaked(session_factory):
    """Rubric-decomposition/vision calls (check_run_id NULL, no instructor
    join possible) are an honest scope gap this ticket doesn't try to
    paper over — they must never appear for ANY instructor."""
    async with session_factory() as session:
        instructor = Instructor(email="i@test.local", display_name="I")
        session.add(instructor)
        await session.commit()
        run = await _make_run(session, instructor)
        session.add(AuditLog(event_type="llm_call", check_run_id=None, payload={}))
        session.add(AuditLog(event_type="llm_call", check_run_id=run.id, payload={}))
        await session.commit()

        page = await list_audit_log(session, instructor.id)
        assert page.total == 1


async def test_direct_manuscript_attribution_is_visible_only_to_its_owner(session_factory):
    """A pre-run lifecycle event remains traceable without leaking instructors."""
    async with session_factory() as session:
        mine = Instructor(email="direct-mine@test.local", display_name="Direct Mine")
        theirs = Instructor(email="direct-theirs@test.local", display_name="Direct Theirs")
        session.add_all([mine, theirs])
        await session.flush()
        manuscript = Manuscript(
            instructor_id=mine.id,
            group_label="Direct Team",
            file_ref="broken.pdf",
            original_filename="broken.pdf",
        )
        session.add(manuscript)
        await session.flush()
        event = AuditLog(
            event_type="manuscript_ingestion_failure_dismissed",
            check_run_id=None,
            manuscript_id=manuscript.id,
            payload={"manuscript_id": manuscript.id},
        )
        session.add(event)
        await session.commit()

        mine_page = await list_audit_log(session, mine.id)
        assert mine_page.total == 1
        assert mine_page.items[0].manuscript_id == manuscript.id
        assert mine_page.items[0].check_run_id is None

        detail = await get_audit_log_detail(session, mine.id, event.id)
        assert detail.manuscript_original_filename == "broken.pdf"

        theirs_page = await list_audit_log(session, theirs.id)
        assert theirs_page.total == 0
        with pytest.raises(NotFoundError):
            await get_audit_log_detail(session, theirs.id, event.id)


async def test_filters_by_check_run_id_and_event_type(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="f@test.local", display_name="F")
        session.add(instructor)
        await session.commit()
        run_a = await _make_run(session, instructor)
        run_b = await _make_run(session, instructor)
        session.add_all(
            [
                AuditLog(event_type="llm_call", check_run_id=run_a.id, payload={}),
                AuditLog(event_type="llm_cache_hit", check_run_id=run_a.id, payload={}),
                AuditLog(event_type="llm_call", check_run_id=run_b.id, payload={}),
            ]
        )
        await session.commit()

        by_run = await list_audit_log(session, instructor.id, check_run_id=run_a.id)
        assert by_run.total == 2

        by_type = await list_audit_log(session, instructor.id, event_type="llm_cache_hit")
        assert by_type.total == 1


async def test_filters_by_event_type_prefix(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="fp@test.local", display_name="FP")
        session.add(instructor)
        await session.commit()
        run = await _make_run(session, instructor)
        session.add_all(
            [
                AuditLog(event_type="llm_call", check_run_id=run.id, payload={}),
                AuditLog(event_type="llm_cache_hit", check_run_id=run.id, payload={}),
                AuditLog(event_type="escalation_resolved", check_run_id=run.id, payload={}),
            ]
        )
        await session.commit()

        ai_calls = await list_audit_log(session, instructor.id, event_type_prefix="llm_")
        assert ai_calls.total == 2


async def test_filters_by_date_range(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="d@test.local", display_name="D")
        session.add(instructor)
        await session.commit()
        run = await _make_run(session, instructor)
        session.add(AuditLog(event_type="llm_call", check_run_id=run.id, payload={}))
        await session.commit()

        now = datetime.now(UTC)
        future_only = await list_audit_log(
            session, instructor.id, date_from=now + timedelta(days=1)
        )
        assert future_only.total == 0

        past_to_now = await list_audit_log(
            session, instructor.id, date_from=now - timedelta(days=1)
        )
        assert past_to_now.total == 1


async def test_pagination(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="p@test.local", display_name="P")
        session.add(instructor)
        await session.commit()
        run = await _make_run(session, instructor)
        for _ in range(5):
            session.add(AuditLog(event_type="llm_call", check_run_id=run.id, payload={}))
        await session.commit()

        page1 = await list_audit_log(session, instructor.id, page=1, page_size=2)
        assert page1.total == 5
        assert len(page1.items) == 2
        page3 = await list_audit_log(session, instructor.id, page=3, page_size=2)
        assert len(page3.items) == 1


async def test_detail_returns_full_payload_and_input_hash(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="det@test.local", display_name="Det")
        session.add(instructor)
        await session.commit()
        run = await _make_run(session, instructor)
        session.add(
            AuditLog(
                event_type="llm_call",
                check_run_id=run.id,
                prompt_version="v1",
                input_hash="abc123",
                payload={
                    "prompt_type": "semantic_grading",
                    "prompt": "hello",
                    "response": {"x": 1},
                },
            )
        )
        await session.commit()
        row_id = (await list_audit_log(session, instructor.id)).items[0].id

        detail = await get_audit_log_detail(session, instructor.id, row_id)
        assert detail.input_hash == "abc123"
        assert detail.payload["prompt"] == "hello"
        assert detail.payload["response"] == {"x": 1}
        # BUG-022 review (backend-critic): the new 3-column unpack in
        # get_audit_log_detail is easy to swap silently — assert the
        # ACTUAL filename, not a copy of group_label ("G").
        assert detail.manuscript_group_label == "G"
        assert detail.manuscript_original_filename == "G-Thesis.pdf"


async def test_detail_404s_for_another_instructors_row(session_factory):
    async with session_factory() as session:
        mine = Instructor(email="m2@test.local", display_name="Mine2")
        theirs = Instructor(email="t2@test.local", display_name="Theirs2")
        session.add_all([mine, theirs])
        await session.commit()
        their_run = await _make_run(session, theirs)
        session.add(AuditLog(event_type="llm_call", check_run_id=their_run.id, payload={}))
        await session.commit()
        their_row_id = (await list_audit_log(session, theirs.id)).items[0].id

        with pytest.raises(NotFoundError):
            await get_audit_log_detail(session, mine.id, their_row_id)
