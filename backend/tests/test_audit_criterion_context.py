"""Producing-format context must not rewrite or misattribute a saved judgment."""

import asyncio
import os
from copy import deepcopy

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.audit.service import get_audit_log_detail
from app.db import sqlalchemy_url
from app.errors import NotFoundError
from app.models.audit import AuditLog
from app.models.instructor import Instructor
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckRun
from app.report.judgment_schemas import JudgmentRecordIn, JudgmentRevisionIn
from app.report.judgments import record_judgment, revise_judgment
from tests.test_initial_judgment_live import settled
from tests.test_schema import _admin_execute, _alembic_config, _swap_db

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="owned Postgres required")
SCRATCH_DB = "veridical_audit_criterion_context_test"


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


async def recorded(factory):
    owner, run, criterion, result, _ = await settled(factory)
    async with factory() as session:
        ack = await record_judgment(
            session,
            run,
            criterion,
            owner,
            JudgmentRecordIn(
                expected_revision=0,
                resolution="mark_fail",
                reason="The design does not address the stated requirements.",
            ),
        )
    return owner, run, criterion, result, ack.entry.entry_id


async def event_copy(session, event, *, payload=None, **changes):
    copy = AuditLog(
        event_type=changes.get("event_type", event.event_type),
        check_run_id=changes.get("check_run_id", event.check_run_id),
        manuscript_id=changes.get("manuscript_id", event.manuscript_id),
        created_at=event.created_at,
        payload=payload if payload is not None else deepcopy(event.payload),
    )
    session.add(copy)
    await session.commit()
    return copy


async def test_context_keeps_producing_version_and_historical_payload_after_revision(factory):
    owner, run, criterion_id, result_id, audit_id = await recorded(factory)
    async with factory() as session:
        event = await session.get(AuditLog, audit_id)
        payload = deepcopy(event.payload)
        criterion = await session.get(Criterion, criterion_id)
        producing = await session.get(Rubric, criterion.rubric_id)
        newer = Rubric(
            instructor_id=owner,
            title="Later format",
            source_file="new.pdf",
            rubric_family_id=producing.rubric_family_id,
            version=2,
            is_active=True,
        )
        session.add(newer)
        await session.flush()
        session.add(
            Criterion(
                rubric_id=newer.id,
                type="semantic",
                text="Different new requirement",
                weight=1,
                position=0,
            )
        )
        await session.commit()
        await revise_judgment(
            session,
            run,
            criterion_id,
            owner,
            JudgmentRevisionIn(
                expected_revision=1,
                expected_judgment_revision=1,
                resolution="mark_pass",
                reason="Further evidence satisfies the requirement.",
            ),
        )
        detail = await get_audit_log_detail(session, owner, audit_id)
        context = detail.criterion_context
        assert context is not None
        assert context.criterion_text == "Explain the design"
        assert (context.check_run_id, context.criterion_id, context.check_result_id) == (
            run,
            criterion_id,
            result_id,
        )
        assert (context.rubric_id, context.rubric_version, context.criterion_position) == (
            producing.id,
            1,
            0,
        )
        assert detail.payload == payload
        assert detail.payload["judgment"]["after"]["outcome"] == "failed"


async def test_legacy_entry_requires_its_recorded_result_and_criterion(factory):
    owner, run, criterion, result, audit_id = await recorded(factory)
    async with factory() as session:
        original = await session.get(AuditLog, audit_id)
        event = await event_copy(
            session,
            original,
            event_type="escalation_resolved",
            payload={
                "criterion_id": criterion,
                "check_result_id": result,
                "resolution": "mark_pass",
                "reason": "",
            },
        )
        detail = await get_audit_log_detail(session, owner, event.id)
        assert detail.criterion_context.criterion_id == criterion
        assert detail.payload["reason"] == ""
        missing = await event_copy(session, event, payload={"criterion_id": criterion})
        assert (await get_audit_log_detail(session, owner, missing.id)).criterion_context is None


@pytest.mark.parametrize("field", ["criterion_id", "check_result_id", "check_run_id"])
@pytest.mark.parametrize("bad", [True, "1", 0, -1, 2**63])
async def test_malformed_saved_identifiers_never_select_context(factory, field, bad):
    owner, _, _, _, audit_id = await recorded(factory)
    async with factory() as session:
        event = await session.get(AuditLog, audit_id)
        payload = deepcopy(event.payload)
        payload["judgment"][field] = bad
        copy = await event_copy(session, event, payload=payload)
        assert (await get_audit_log_detail(session, owner, copy.id)).criterion_context is None


@pytest.mark.parametrize("field", ["criterion_id", "check_result_id", "check_run_id"])
async def test_conflicting_outer_identifiers_are_not_ignored(factory, field):
    owner, _, _, _, audit_id = await recorded(factory)
    async with factory() as session:
        event = await session.get(AuditLog, audit_id)
        payload = deepcopy(event.payload)
        payload[field] = payload["judgment"][field] + 1
        copy = await event_copy(session, event, payload=payload)
        assert (await get_audit_log_detail(session, owner, copy.id)).criterion_context is None


async def test_foreign_or_other_run_result_cannot_supply_requirement(factory):
    owner, _, _, _, audit_id = await recorded(factory)
    other, other_run, other_criterion, other_result, _ = await recorded(factory)
    async with factory() as session:
        event = await session.get(AuditLog, audit_id)
        with pytest.raises(NotFoundError):
            await get_audit_log_detail(session, other, audit_id)
        payload = deepcopy(event.payload)
        payload["judgment"].update(criterion_id=other_criterion, check_result_id=other_result)
        copy = await event_copy(session, event, payload=payload)
        assert (await get_audit_log_detail(session, owner, copy.id)).criterion_context is None
        other_row = await session.get(CheckRun, other_run)
        copy = await event_copy(session, event, manuscript_id=other_row.manuscript_id)
        assert (await get_audit_log_detail(session, owner, copy.id)).criterion_context is None


async def test_wrong_rubric_version_and_foreign_format_fail_closed(factory):
    owner, run, criterion_id, _, audit_id = await recorded(factory)
    async with factory() as session:
        criterion = await session.get(Criterion, criterion_id)
        outsider = Instructor(email="context-foreign@test.local", display_name="Foreign")
        session.add(outsider)
        await session.flush()
        foreign = Rubric(
            instructor_id=outsider.id, title="Private foreign title", source_file="f.pdf"
        )
        session.add(foreign)
        await session.flush()
        criterion.rubric_id = foreign.id
        await session.commit()
        assert (await get_audit_log_detail(session, owner, audit_id)).criterion_context is None
        parent = await session.get(CheckRun, run)
        parent.rubric_id = foreign.id
        await session.commit()
        assert (await get_audit_log_detail(session, owner, audit_id)).criterion_context is None


async def test_blank_requirement_and_ineligible_event_do_not_invent_context(factory):
    owner, _, criterion_id, _, audit_id = await recorded(factory)
    async with factory() as session:
        event = await session.get(AuditLog, audit_id)
        copy = await event_copy(session, event, event_type="llm_call")
        assert (await get_audit_log_detail(session, owner, copy.id)).criterion_context is None
        criterion = await session.get(Criterion, criterion_id)
        criterion.text = " \n "
        await session.commit()
        assert (await get_audit_log_detail(session, owner, audit_id)).criterion_context is None
