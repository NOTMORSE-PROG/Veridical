"""F3/F8: pending DTO epochs are coherent locked reads, including reused sessions."""

import asyncio
import os

import pytest
from sqlalchemy import delete, select

from app.errors import NotFoundError
from app.models.run import CheckResult, ReadinessReport
from app.report.assessment import lock_assessment
from app.report.service import list_escalated_for_run, resolve_escalation_for_run
from tests.test_assessment_revision_live import REASON, seed
from tests.test_assessment_revision_live import factory as factory
from tests.test_assessment_revision_live import scratch_url as scratch_url

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="owned PostgreSQL required"
)


async def test_pending_read_holds_parent_through_dto_then_releases_before_writer(
    factory, monkeypatch
):
    owner, run, ids, _ = await seed(factory)
    from app.report import service

    original = service.list_escalated
    held, release = asyncio.Event(), asyncio.Event()

    async def paused(session, run_id):
        rows = await original(session, run_id)
        held.set()
        await release.wait()
        return rows

    monkeypatch.setattr(service, "list_escalated", paused)

    async def read():
        async with factory() as session:
            return await list_escalated_for_run(session, run, owner)

    async def write():
        async with factory() as session:
            return await resolve_escalation_for_run(
                session, run, ids[0], owner, "mark_pass", REASON, expected_revision=0
            )

    reader = asyncio.create_task(read())
    await asyncio.wait_for(held.wait(), 5)
    writer = asyncio.create_task(write())
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(writer), 0.1)
    finally:
        release.set()
    pending, saved = await asyncio.wait_for(asyncio.gather(reader, writer), 5)
    assert {item.assessment_revision for item in pending} == {0}
    assert {item.check_result_id for item in pending} == set(ids)
    assert saved.report.assessment_revision == 1


async def test_reused_session_refreshes_pending_children_alongside_parent_epoch(factory):
    owner, run, ids, _ = await seed(factory)
    async with factory() as stale:
        old = await stale.get(CheckResult, ids[0])
        await stale.commit()
        async with factory() as writer:
            parent = await lock_assessment(writer, run, write=True)
            row = await writer.get(CheckResult, ids[0])
            row.detail = {**row.detail, "reason": "Fresh recorded uncertainty"}
            parent.assessment_revision += 1
            await writer.commit()
        assert old.detail["reason"] == "Recorded uncertainty"
        pending = await list_escalated_for_run(stale, run, owner)
        row = next(item for item in pending if item.check_result_id == ids[0])
        assert row.assessment_revision == 1 and row.reason == "Fresh recorded uncertainty"


async def test_projection_failure_and_foreign_owner_release_transaction(factory, monkeypatch):
    owner, run, _, _ = await seed(factory)
    from app.report import service

    def broken(*args, **kwargs):
        raise ValueError("Controlled projection refusal")

    monkeypatch.setattr(service, "_to_escalated_out", broken)
    async with factory() as reader:
        with pytest.raises(ValueError, match="Controlled projection refusal"):
            await list_escalated_for_run(reader, run, owner)
        assert not reader.in_transaction()
        with pytest.raises(NotFoundError):
            await list_escalated_for_run(reader, run, owner + 1000000)
        assert not reader.in_transaction()
        async with factory() as writer:
            parent = await writer.scalar(
                select(ReadinessReport)
                .where(ReadinessReport.check_run_id == run)
                .with_for_update(nowait=True)
            )
            assert parent.assessment_revision == 0
            await writer.rollback()


async def test_legacy_pending_without_parent_retains_null_epoch(factory):
    owner, run, ids, _ = await seed(factory)
    async with factory() as session:
        await session.execute(delete(ReadinessReport).where(ReadinessReport.check_run_id == run))
        await session.commit()
        pending = await list_escalated_for_run(session, run, owner)
        assert {item.check_result_id for item in pending} == set(ids)
        assert all(item.assessment_revision is None for item in pending)
        assert not session.in_transaction()
