"""V-018 live-DB tests: `create_check_run`'s validations (ownership,
ingestion/activation preconditions) and the deprecated queue-position contract.
"""

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.config import get_settings
from app.errors import ConflictError, NotFoundError
from app.models.audit import AuditLog
from app.models.enums import CheckRunStatus, IngestStatus, LLMMode, ReadinessStatus
from app.models.group import Group, Program
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun, ReadinessReport
from app.pipeline.machine import _transition_after_boundary
from app.pipeline.service import cancel_check_run, create_check_run, queue_position

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
                "TRUNCATE audit_log, readiness_report, check_run, criterion, "
                "rubric, manuscript, instructor "
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
    """backend review finding (V-042, P1, live-reproduced): a purged
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


async def test_queue_position_is_unavailable_even_for_creation_ordered_runs(session_factory):
    instructor_id, manuscript_id, rubric_id = await _seed_instructor_manuscript_rubric(
        session_factory
    )
    async with session_factory() as session:
        first = await create_check_run(session, instructor_id, manuscript_id, rubric_id)
        # Distinct uploads queue independently; duplicate clicks now reuse the
        # active run for an identical manuscript/rubric/mode.
        copies = [
            Manuscript(
                instructor_id=instructor_id,
                group_label="G",
                file_ref=f"copy-{i}.pdf",
                ingest_status=IngestStatus.done,
            )
            for i in range(2)
        ]
        session.add_all(copies)
        await session.commit()
        second = await create_check_run(session, instructor_id, copies[0].id, rubric_id)
        third = await create_check_run(session, instructor_id, copies[1].id, rubric_id)

        assert await queue_position(session, first) is None
        assert await queue_position(session, second) is None
        assert await queue_position(session, third) is None


async def test_bug352_two_instructor_waiting_contract_after_a1_is_claimed(session_factory):
    """Live selector/claim exercise; no pipeline or provider execution."""
    from app.pipeline.worker import pick_next_runnable

    owner_a, manuscript_a, rubric_a = await _seed_instructor_manuscript_rubric(session_factory)
    _, manuscript_b, rubric_b = await _seed_instructor_manuscript_rubric(session_factory)
    base = datetime.now(UTC) - timedelta(minutes=1)
    async with session_factory() as session:
        copies = [
            Manuscript(instructor_id=owner_a, group_label="A", file_ref=f"a-{i}.pdf")
            for i in range(2)
        ]
        session.add_all(copies)
        await session.flush()
        runs = [
            CheckRun(
                manuscript_id=manuscript_id,
                rubric_id=rubric_id,
                created_at=base + timedelta(seconds=index),
                llm_mode=LLMMode.fake,
            )
            for index, (manuscript_id, rubric_id) in enumerate(
                [
                    (manuscript_a, rubric_a),
                    *[(copy.id, rubric_a) for copy in copies],
                    (manuscript_b, rubric_b),
                ]
            )
        ]
        session.add_all(runs)
        await session.commit()
        assert [await queue_position(session, run) for run in runs] == [None] * 4

        picked = await pick_next_runnable(session, get_settings())
        assert picked is not None and picked[0].id == runs[0].id
        assert picked[0].claimed_at is not None
        # Represent A1's recorded turn without executing a fixture pipeline.
        picked[0].started_at = datetime.now(UTC)
        picked[0].status = CheckRunStatus.semantic
        await session.commit()
        assert [await queue_position(session, run) for run in runs] == [None] * 4
        second = await pick_next_runnable(session, get_settings())
        assert second is not None and second[0].id == runs[3].id
        assert second[0].llm_mode == LLMMode.fake


async def test_concurrent_identical_creation_reuses_one_active_run(session_factory):
    args = await _seed_instructor_manuscript_rubric(session_factory)

    async def create():
        async with session_factory() as session:
            return (await create_check_run(session, *args)).id

    ids = await asyncio.gather(create(), create())
    assert ids[0] == ids[1]
    async with session_factory() as session:
        assert await session.scalar(select(func.count(CheckRun.id))) == 1
        run = await session.get(CheckRun, ids[0])
        run.status = CheckRunStatus.done
        await session.commit()
        assert (await create_check_run(session, *args)).id != ids[0]


async def test_queue_position_is_none_once_terminal(session_factory):
    instructor_id, manuscript_id, rubric_id = await _seed_instructor_manuscript_rubric(
        session_factory
    )
    async with session_factory() as session:
        run = await create_check_run(session, instructor_id, manuscript_id, rubric_id)
        run.status = "done"
        await session.commit()
        assert await queue_position(session, run) is None


async def test_concurrent_queued_cancellation_writes_one_event_pair(session_factory):
    """Two requests may race, but the immutable history records one action."""
    instructor_id, manuscript_id, rubric_id = await _seed_instructor_manuscript_rubric(
        session_factory
    )
    async with session_factory() as session:
        run = await create_check_run(session, instructor_id, manuscript_id, rubric_id)
        run_id = run.id

    async def cancel_once():
        async with session_factory() as session:
            return await cancel_check_run(session, run_id, instructor_id)

    first, second = await asyncio.gather(cancel_once(), cancel_once())
    assert first.status == CheckRunStatus.cancelled
    assert second.status == CheckRunStatus.cancelled

    async with session_factory() as verify:
        event_counts = dict(
            (
                await verify.execute(
                    select(AuditLog.event_type, func.count())
                    .where(
                        AuditLog.check_run_id == run_id,
                        AuditLog.event_type.in_(
                            ("check_run_cancel_requested", "check_run_cancelled")
                        ),
                    )
                    .group_by(AuditLog.event_type)
                )
            ).all()
        )
        assert event_counts == {
            "check_run_cancel_requested": 1,
            "check_run_cancelled": 1,
        }


async def test_cancellation_advances_past_a_completed_persisted_stage(session_factory):
    """The cancellation record names the next stage when the current one is done."""
    instructor_id, manuscript_id, rubric_id = await _seed_instructor_manuscript_rubric(
        session_factory
    )
    async with session_factory() as setup:
        run = await create_check_run(setup, instructor_id, manuscript_id, rubric_id)
        run.status = CheckRunStatus.structural
        run.stage_status = {"stages": {"structural": {"status": "done"}}}
        await setup.commit()
        run_id = run.id

    async with session_factory() as request:
        requested = await cancel_check_run(request, run_id, instructor_id)
        assert requested.status == CheckRunStatus.structural
        assert requested.cancel_requested_at is not None

    async with session_factory() as worker:
        run = await worker.get(CheckRun, run_id)
        advanced = await _transition_after_boundary(
            worker, run, CheckRunStatus.structural, get_settings()
        )
        assert advanced is False

    async with session_factory() as verify:
        run = await verify.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.cancelled
        assert run.stage_status["cancellation"]["stopped_before"] == "semantic"
        cancelled_event = await verify.scalar(
            select(AuditLog).where(
                AuditLog.check_run_id == run_id,
                AuditLog.event_type == "check_run_cancelled",
            )
        )
        assert cancelled_event.payload["stopped_before"] == "semantic"


async def test_completion_and_cancellation_have_one_atomic_winner(session_factory):
    """The terminal row, report, timestamp, and audit trail cannot disagree."""
    instructor_id, manuscript_id, rubric_id = await _seed_instructor_manuscript_rubric(
        session_factory
    )
    async with session_factory() as setup:
        run = await create_check_run(setup, instructor_id, manuscript_id, rubric_id)
        run.status = CheckRunStatus.aggregating
        run.stage_status = {"stages": {"aggregating": {"status": "done"}}}
        setup.add(
            ReadinessReport(
                check_run_id=run.id,
                composite_score=Decimal("80"),
                status=ReadinessStatus.ready,
            )
        )
        await setup.commit()
        run_id = run.id

    async def request_cancel():
        async with session_factory() as session:
            try:
                await cancel_check_run(session, run_id, instructor_id)
            except ConflictError:
                return "completion"
            return "cancellation"

    async def finish_run():
        async with session_factory() as session:
            run = await session.get(CheckRun, run_id)
            advanced = await _transition_after_boundary(
                session, run, CheckRunStatus.aggregating, get_settings()
            )
            return "completion" if advanced else "cancellation"

    await asyncio.gather(request_cancel(), finish_run())

    async with session_factory() as verify:
        run = await verify.get(CheckRun, run_id)
        report = await verify.scalar(
            select(ReadinessReport).where(ReadinessReport.check_run_id == run_id)
        )
        event_counts = dict(
            (
                await verify.execute(
                    select(AuditLog.event_type, func.count())
                    .where(
                        AuditLog.check_run_id == run_id,
                        AuditLog.event_type.in_(
                            ("check_run_cancel_requested", "check_run_cancelled")
                        ),
                    )
                    .group_by(AuditLog.event_type)
                )
            ).all()
        )

        if run.status == CheckRunStatus.done:
            assert run.cancel_requested_at is None
            assert report is not None
            assert event_counts == {}
        else:
            assert run.status == CheckRunStatus.cancelled
            assert run.cancel_requested_at is not None
            assert report is None
            assert event_counts == {
                "check_run_cancel_requested": 1,
                "check_run_cancelled": 1,
            }


async def test_cancelling_a_parked_run_finishes_without_waiting_for_resume(session_factory):
    """A parked worker is absent, so the request endpoint owns the safe stop."""
    instructor_id, manuscript_id, rubric_id = await _seed_instructor_manuscript_rubric(
        session_factory
    )
    async with session_factory() as setup:
        run = await create_check_run(setup, instructor_id, manuscript_id, rubric_id)
        run.status = CheckRunStatus.semantic
        run.stage_status = {
            "blocked": {
                "code": "api_down",
                "message": "The service is unavailable.",
                "resume_at": None,
            }
        }
        await setup.commit()
        run_id = run.id

    async with session_factory() as session:
        cancelled = await cancel_check_run(session, run_id, instructor_id)
        assert cancelled.status == CheckRunStatus.cancelled
        assert cancelled.stage_status["cancellation"]["stopped_before"] == "semantic"

    async with session_factory() as verify:
        event_types = list(
            (
                await verify.scalars(
                    select(AuditLog.event_type)
                    .where(
                        AuditLog.check_run_id == run_id,
                        AuditLog.event_type.in_(
                            ("check_run_cancel_requested", "check_run_cancelled")
                        ),
                    )
                    .order_by(AuditLog.id)
                )
            ).all()
        )
        assert event_types == ["check_run_cancel_requested", "check_run_cancelled"]
