"""D-034 initial judgments exercise real locks, scoring and immutable history."""

import asyncio
import os
from copy import deepcopy
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.db import sqlalchemy_url
from app.errors import ConflictError, NotFoundError
from app.models.rubric import Criterion
from app.models.run import CheckResult
from app.report.judgment_schemas import JudgmentRecordIn, JudgmentRevisionIn
from app.report.judgments import (
    get_judgment_context,
    get_judgment_history,
    record_judgment,
    revise_judgment,
)
from app.report.service import decide_report, reopen_report
from app.share.service import _to_public_report
from tests.test_assessment_revision_live import REASON, seed, stored
from tests.test_schema import _admin_execute, _alembic_config, _swap_db

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="owned Postgres required")
SCRATCH_DB = "veridical_initial_judgment_486_test"


@pytest.fixture(scope="module")
def scratch_url():
    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    try:
        url = _swap_db(base, SCRATCH_DB)
        command.upgrade(_alembic_config(url), "head")
        yield url
    finally:
        asyncio.run(_admin_execute(base, f'DROP DATABASE "{SCRATCH_DB}" WITH (FORCE)'))


@pytest.fixture()
async def factory(scratch_url):
    engine = create_async_engine(sqlalchemy_url(scratch_url))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def settled(factory, *, levelled=False):
    owner, run, ids, _ = await seed(factory)
    result_id = ids[1 if levelled else 0]
    async with factory() as session:
        result = await session.get(CheckResult, result_id)
        result.outcome, result.score = "passed", Decimal("100")
        result.detail = {
            "basis": "llm",
            "verdict": "pass",
            "reasoning": "Original automated basis",
            "evidence": [],
            "retained_context": {"value": "unchanged"},
        }
        await session.commit()
        return owner, run, result.criterion_id, result_id, deepcopy(result.detail)


def entry(epoch=0, resolution="mark_fail", **kwargs):
    return JudgmentRecordIn(expected_revision=epoch, resolution=resolution, reason=REASON, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resolution,levelled,level",
    [
        ("mark_fail", False, None),
        ("mark_pass", False, None),
        ("needs_document", False, None),
        ("mark_level", True, 2),
    ],
)
async def test_initial_judgment_preserves_original_and_private_history(
    factory, resolution, levelled, level
):
    owner, run, criterion, result_id, original = await settled(factory, levelled=levelled)
    async with factory() as session:
        context = await get_judgment_context(session, run, criterion, owner)
        assert context.can_record and not context.can_revise and context.current is None
        ack = await record_judgment(
            session, run, criterion, owner, entry(resolution=resolution, level=level)
        )
    assert ack.context.assessment_revision == 1
    assert not ack.context.can_record and ack.context.can_revise
    assert ack.context.current.judgment_revision == 1
    assert ack.entry.schema_version == 2 and ack.entry.before is None
    assert ack.entry.action == "judgment_recorded"
    assert ack.entry.original_assessment == context.original_assessment
    assert (
        ack.entry.after.actor_id == owner and ack.entry.performed_at == ack.entry.after.recorded_at
    )
    parent, rows, audits = await stored(factory, run)
    current = next(row for row in rows if row.id == result_id)
    assert parent.assessment_revision == 1 and current.judgment_revision == 1
    assert current.detail["original_result"] == {"outcome": "passed", "score": 100, "level": None}
    assert all(current.detail[key] == value for key, value in original.items())
    assert len([row for row in audits if row.event_type == "judgment_recorded"]) == 1
    async with factory() as session:
        history = await get_judgment_history(
            session, run, criterion, owner, expected_revision=1, expected_judgment_revision=1
        )
    assert history.entries == [ack.entry] and not history.history_incomplete
    public = str(_to_public_report(ack.report).model_dump(mode="json"))
    assert (
        REASON not in public
        and "original_assessment" not in public
        and "judgment_revision" not in public
    )
    if resolution == "needs_document":
        assert current.outcome == "not_applicable" and current.score is None
    if resolution == "mark_pass":
        assert current.outcome == "passed" and current.score == 100


@pytest.mark.asyncio
async def test_record_then_revision_keeps_initial_automated_grade_and_distinct_events(factory):
    owner, run, criterion, result_id, _ = await settled(factory)
    async with factory() as session:
        initial = await record_judgment(session, run, criterion, owner, entry())
        revised = await revise_judgment(
            session,
            run,
            criterion,
            owner,
            JudgmentRevisionIn(
                expected_revision=1,
                expected_judgment_revision=1,
                resolution="mark_pass",
                reason="Further source evidence supports this corrected instructor judgment.",
            ),
        )
    assert revised.context.current.judgment_revision == 2
    assert revised.context.original_assessment == initial.context.original_assessment
    async with factory() as session:
        history = await get_judgment_history(
            session, run, criterion, owner, expected_revision=2, expected_judgment_revision=2
        )
    assert [e.action for e in history.entries] == ["judgment_revised", "judgment_recorded"]
    assert [e.schema_version for e in history.entries] == [1, 2]
    assert history.entries[0].before == initial.entry.after and history.entries[1].before is None
    _, rows, _ = await stored(factory, run)
    assert (
        next(row for row in rows if row.id == result_id).detail["original_result"]["outcome"]
        == "passed"
    )


@pytest.mark.asyncio
async def test_initial_named_grade_retains_database_rounded_normalization(factory):
    owner, run, criterion, _, _ = await settled(factory, levelled=True)
    async with factory() as session:
        producing = await session.get(Criterion, criterion)
        producing.levels = [
            {"level": 0, "name": "Absent", "points": 0, "descriptor": "No evidence"},
            {"level": 2, "name": "Developing", "points": 1, "descriptor": "Some evidence"},
            {"level": 5, "name": "Complete", "points": 3, "descriptor": "Complete evidence"},
        ]
        await session.commit()
        ack = await record_judgment(
            session, run, criterion, owner, entry(resolution="mark_level", level=2)
        )
    assert ack.context.current.score == 33.33
    assert ack.context.current.level.name == "Developing"
    assert ack.context.current.level.points == 1 and ack.context.current.level.max_points == 3
    async with factory() as session:
        history = await get_judgment_history(
            session, run, criterion, owner, expected_revision=1, expected_judgment_revision=1
        )
    assert history.entries[0].after == ack.context.current and not history.history_incomplete


@pytest.mark.asyncio
async def test_stale_duplicate_foreign_and_decided_requests_write_nothing(factory):
    owner, run, criterion, _, _ = await settled(factory)
    async with factory() as session:
        with pytest.raises(NotFoundError):
            await record_judgment(session, run, criterion, owner + 999, entry())
        with pytest.raises(NotFoundError):
            await record_judgment(session, run, criterion + 999, owner, entry())
        with pytest.raises(ConflictError):
            await record_judgment(session, run, criterion, owner, entry(epoch=1))
        await decide_report(session, run, owner, "returned", REASON, expected_revision=0)
        with pytest.raises(ConflictError, match="Reopen"):
            await record_judgment(session, run, criterion, owner, entry(epoch=1))
        await reopen_report(session, run, owner, REASON, expected_revision=1)
        await record_judgment(session, run, criterion, owner, entry(epoch=2))
        with pytest.raises(ConflictError):
            await record_judgment(session, run, criterion, owner, entry(epoch=3))
    parent, rows, audits = await stored(factory, run)
    assert parent.assessment_revision == 3 and parent.decision is None
    assert sum(row.judgment_revision == 1 for row in rows) == 1
    assert len([row for row in audits if row.event_type == "judgment_recorded"]) == 1


@pytest.mark.asyncio
async def test_scoring_failure_rolls_back_original_grade_epoch_and_event(factory, monkeypatch):
    import app.report.service as service

    owner, run, criterion, result_id, original = await settled(factory)

    async def fail(*args, **kwargs):
        raise RuntimeError("Controlled scoring failure")

    monkeypatch.setattr(service, "aggregate_and_score", fail)
    async with factory() as session:
        with pytest.raises(RuntimeError, match="Controlled scoring"):
            await record_judgment(session, run, criterion, owner, entry())
    parent, rows, audits = await stored(factory, run)
    current = next(row for row in rows if row.id == result_id)
    assert parent.assessment_revision == 0 and current.judgment_revision is None
    assert current.detail == original and current.outcome == "passed" and current.score == 100
    assert not audits


@pytest.mark.asyncio
async def test_two_first_judgments_at_one_epoch_cannot_both_commit(factory):
    owner, run, criterion, _, _ = await settled(factory)

    async def write(resolution):
        async with factory() as session:
            return await record_judgment(
                session, run, criterion, owner, entry(resolution=resolution)
            )

    outcomes = await asyncio.gather(write("mark_pass"), write("mark_fail"), return_exceptions=True)
    assert sum(isinstance(value, ConflictError) for value in outcomes) == 1
    assert sum(not isinstance(value, BaseException) for value in outcomes) == 1
    parent, rows, audits = await stored(factory, run)
    assert parent.assessment_revision == 1 and rows[0].judgment_revision == 1
    assert len([row for row in audits if row.event_type == "judgment_recorded"]) == 1
