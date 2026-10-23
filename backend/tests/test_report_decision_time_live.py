"""V-081: one database time source and stale-reopen protection."""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

import app.report.service as service
from app.errors import ConflictError
from app.models.audit import AuditLog
from app.models.run import ReadinessReport
from tests.test_report_decision_live import (
    _clean_tables as _clean_tables,
)
from tests.test_report_decision_live import (
    _seed_decidable_run,
)
from tests.test_report_decision_live import (
    scratch_url as scratch_url,
)
from tests.test_report_decision_live import (
    session_factory as session_factory,
)

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="live PostgreSQL required")


async def test_decision_and_audit_use_one_database_time_despite_host_clock_skew(
    session_factory, monkeypatch
):
    async with session_factory() as session:
        owner, run = await _seed_decidable_run(session)
        transaction_start = await session.scalar(select(func.now()))
        await session.execute(text("select pg_sleep(0.01)"))
        database_before = await session.scalar(select(func.clock_timestamp()))

        class SkewedHostClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return (database_before + timedelta(seconds=310)).astimezone(tz or UTC)

        monkeypatch.setattr(service, "datetime", SkewedHostClock, raising=False)
        decided = await service.decide_report(
            session, run.id, owner.id, "returned", None, expected_revision=0
        )
        database_after = await session.scalar(select(func.clock_timestamp()))
        rows = (
            await session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.check_run_id == run.id,
                    AuditLog.event_type.in_(("verdict_computed", "report_decided")),
                )
                .order_by(AuditLog.id.desc())
                .limit(2)
            )
        ).all()
        assert {row.event_type for row in rows} == {"report_decided", "verdict_computed"}
        assert all(row.created_at == decided.decided_at for row in rows)
        assert transaction_start < database_before <= decided.decided_at <= database_after


async def test_reopen_audit_uses_action_time_not_start_of_a_long_transaction(session_factory):
    async with session_factory() as session:
        owner, run = await _seed_decidable_run(session)
        await service.decide_report(
            session, run.id, owner.id, "returned", None, expected_revision=0
        )
        transaction_start = await session.scalar(select(func.now()))
        await session.execute(text("select pg_sleep(0.01)"))
        before_reopen = await session.scalar(select(func.clock_timestamp()))
        reopened = await service.reopen_report(
            session,
            run.id,
            owner.id,
            "Review the newly supplied source evidence.",
            expected_revision=1,
        )
        after_reopen = await session.scalar(select(func.clock_timestamp()))
        row = await session.scalar(
            select(AuditLog).where(
                AuditLog.check_run_id == run.id, AuditLog.event_type == "report_reopened"
            )
        )
        assert reopened.decision is None
        assert row is not None
        assert transaction_start < before_reopen <= row.created_at <= after_reopen


async def test_stale_reopen_cannot_erase_a_newer_decision(session_factory, monkeypatch):
    async with session_factory() as seed:
        owner, run = await _seed_decidable_run(seed)
        await service.decide_report(seed, run.id, owner.id, "returned", None, expected_revision=0)
        owner_id, run_id = owner.id, run.id

    stale_has_read = asyncio.Event()
    release_stale = asyncio.Event()
    import app.report.assessment as assessment

    original_lock = assessment.lock_assessment

    async def barrier_get(session, check_run_id, *, write=False):
        if write and asyncio.current_task().get_name() == "stale-reopen":
            stale_has_read.set()
            await asyncio.wait_for(release_stale.wait(), timeout=10)
        return await original_lock(session, check_run_id, write=write)

    monkeypatch.setattr(assessment, "lock_assessment", barrier_get)

    async def stale_request():
        async with session_factory() as session:
            try:
                await service.reopen_report(
                    session,
                    run_id,
                    owner_id,
                    "An earlier review request arrived late.",
                    expected_revision=1,
                )
            except ConflictError:
                await session.rollback()
                return "conflict"
            return "incorrectly reopened"

    stale = asyncio.create_task(stale_request(), name="stale-reopen")
    try:
        await asyncio.wait_for(stale_has_read.wait(), timeout=10)
        async with session_factory() as winner:
            await service.reopen_report(
                winner,
                run_id,
                owner_id,
                "Recheck the source before recording a new decision.",
                expected_revision=1,
            )
            # Keep the same decision/note so decided_at must identify the
            # particular decision snapshot, not only its outcome label.
            latest = await service.decide_report(
                winner, run_id, owner_id, "returned", None, expected_revision=2
            )
        release_stale.set()
        assert await asyncio.wait_for(stale, timeout=10) == "conflict"
        async with session_factory() as verify:
            report = await verify.scalar(
                select(ReadinessReport).where(ReadinessReport.check_run_id == run_id)
            )
            assert report.decision.value == "returned"
            assert report.decided_at == latest.decided_at
            assert (
                await verify.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.check_run_id == run_id, AuditLog.event_type == "report_reopened"
                    )
                )
                == 1
            )
    finally:
        release_stale.set()
        if not stale.done():
            stale.cancel()
        await asyncio.gather(stale, return_exceptions=True)
