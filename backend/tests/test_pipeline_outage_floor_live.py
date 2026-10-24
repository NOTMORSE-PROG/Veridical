"""F3/F8: terminal service outages retain a usable, honestly partial report."""

import asyncio
import copy
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.checks.semantic import record_ungraded
from app.db import sqlalchemy_url
from app.errors import ApiDownError, QuotaExhaustedError
from app.llm.fake import FakeLLMClient
from app.models.enums import CheckKind, CheckRunStatus, ReadinessStatus, ResultOutcome
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion
from app.models.run import CheckResult, CheckRun, ReadinessReport
from app.pipeline import machine
from app.pipeline.guards import try_guard
from tests.test_pipeline_machine_live import _FlakyThenFineLLM, _seed

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="integration: needs a live Postgres"
)


@pytest.fixture(scope="module")
def outage_url():
    from alembic import command
    from tests.test_schema import _admin_execute, _alembic_config, _swap_db

    base = os.environ["DATABASE_URL"]
    name = f"veridical_outage468_{uuid4().hex[:12]}"
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{name}"'))
    url = _swap_db(base, name)
    command.upgrade(_alembic_config(url), "head")
    try:
        yield url
    finally:
        asyncio.run(_admin_execute(base, f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture
async def session_factory(outage_url):
    engine = create_async_engine(sqlalchemy_url(outage_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(text("TRUNCATE instructor RESTART IDENTITY CASCADE"))
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


class OutageClient(_FlakyThenFineLLM):
    def __init__(self, fail_after=0, before_outage=None):
        super().__init__(fail_after)
        self.before_outage = before_outage
        self.prefix = None

    async def complete(self, *args, **kwargs):
        try:
            return await super().complete(*args, **kwargs)
        except QuotaExhaustedError as exc:
            if self.before_outage is not None:
                await self.before_outage()
            raise ApiDownError("private-provider-canary: controlled service outage") from exc


async def semantic_rows(session, run_id):
    return list(
        await session.scalars(
            select(CheckResult)
            .where(CheckResult.check_run_id == run_id, CheckResult.kind == CheckKind.semantic)
            .order_by(CheckResult.id)
        )
    )


def snapshot(row):
    return (row.id, row.criterion_id, row.outcome, row.score, copy.deepcopy(row.detail))


@pytest.mark.parametrize("quota_floor", [True, False])
async def test_total_outage_finishes_without_reinterpreting_quota_policy(
    session_factory, tmp_path, monkeypatch, quota_floor
):
    run_id, criteria, settings = await _seed(session_factory, tmp_path, monkeypatch)
    settings = settings.model_copy(update={"pipeline_degrade_on_quota": quota_floor})
    client = OutageClient()
    async with session_factory() as session:
        run = await session.get(CheckRun, run_id)
        await machine.run_check_run(session, run, settings, client)
    async with session_factory() as observer:
        run = await observer.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.done
        assert "blocked" not in run.stage_status
        assert run.stage_status["stages"]["semantic"] == {
            "status": "done",
            "n_criteria": 2,
            "degraded_count": 2,
            "degraded_code": "api_down",
        }
        rows = await semantic_rows(observer, run_id)
        assert {row.criterion_id for row in rows} == set(criteria[1:])
        for row in rows:
            assert row.outcome == ResultOutcome.api_down and row.score is None
            assert row.detail == {
                "basis": "not-graded",
                "reason": settings.pipeline_api_down_degraded_reason,
            }
            assert "private-provider-canary" not in str(row.detail)
        report = await observer.scalar(
            select(ReadinessReport).where(ReadinessReport.check_run_id == run_id)
        )
        assert report is not None and report.status == ReadinessStatus.needs_review
        assert (
            await observer.scalar(
                select(CheckResult.id).where(
                    CheckResult.check_run_id == run_id, CheckResult.kind == CheckKind.structural
                )
            )
            is not None
        )
        assert run.stage_status["stages"]["integrity"]["status"] == "done"
    assert client.calls > 0


async def test_outage_preserves_completed_batch_and_never_regrades_finished_run(
    session_factory, tmp_path, monkeypatch
):
    run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)
    settings = settings.model_copy(update={"semantic_batch_max_criteria": 1})
    prefix = []

    async def remember_prefix():
        if not prefix:
            async with session_factory() as observer:
                prefix.extend(snapshot(row) for row in await semantic_rows(observer, run_id))

    client = OutageClient(fail_after=3, before_outage=remember_prefix)
    async with session_factory() as session:
        await machine.run_check_run(session, await session.get(CheckRun, run_id), settings, client)
    assert len(prefix) == 1 and prefix[0][2] == ResultOutcome.passed
    async with session_factory() as observer:
        rows = await semantic_rows(observer, run_id)
        assert snapshot(rows[0]) == prefix[0]
        assert len(rows) == 2 and rows[1].outcome == ResultOutcome.api_down
        run = await observer.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.done
        assert run.stage_status["stages"]["semantic"]["degraded_count"] == 1
        saved = [snapshot(row) for row in rows]

    class HealthyClient(FakeLLMClient):
        calls = 0

        async def complete(self, *args, **kwargs):
            self.calls += 1
            return await super().complete(*args, **kwargs)

    healthy = HealthyClient()
    async with session_factory() as session:
        await machine.run_check_run(session, await session.get(CheckRun, run_id), settings, healthy)
    async with session_factory() as observer:
        assert [snapshot(row) for row in await semantic_rows(observer, run_id)] == saved
    assert healthy.calls == 0


@pytest.mark.parametrize("later_stop", ["failure", "cancel"])
async def test_outage_records_do_not_promise_report_before_later_stop(
    session_factory, tmp_path, monkeypatch, later_stop
):
    run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)
    attempted_later = []

    async def stop_integrity(session, run, *_args):
        attempted_later.append(run.id)
        if later_stop == "failure":
            raise machine.TerminalFailure("controlled_integrity_failure", "Controlled failure")
        from app.pipeline.service import cancel_check_run

        async with session_factory() as instructor_session:
            manuscript = await instructor_session.get(Manuscript, run.manuscript_id)
            await cancel_check_run(instructor_session, run.id, manuscript.instructor_id)

    monkeypatch.setattr(machine, "_run_integrity_stage", stop_integrity)
    async with session_factory() as session:
        await machine.run_check_run(
            session, await session.get(CheckRun, run_id), settings, OutageClient()
        )
    assert attempted_later == [run_id]
    async with session_factory() as observer:
        run = await observer.get(CheckRun, run_id)
        expected = CheckRunStatus.failed if later_stop == "failure" else CheckRunStatus.cancelled
        assert run.status == expected
        rows = await semantic_rows(observer, run_id)
        assert len(rows) == 2
        assert all(row.outcome == ResultOutcome.api_down for row in rows)
        assert all("If a report is produced" in row.detail["reason"] for row in rows)
        assert not await observer.scalar(
            select(ReadinessReport.id).where(ReadinessReport.check_run_id == run_id)
        )


@pytest.mark.parametrize("loss", ["replacement", "busy_guard", "control_flow_loss"])
async def test_queued_outage_rows_and_metadata_do_not_publish_after_ownership_loss(
    session_factory, tmp_path, monkeypatch, loss
):
    run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as setup:
        run = await setup.get(CheckRun, run_id)
        run.status = CheckRunStatus.semantic
        run.claimed_at = await setup.scalar(select(func.clock_timestamp()))
        original = run.claimed_at
        await setup.commit()

    async def token():
        return original

    real_degrade = machine._degrade_pending_semantic
    queued = []
    async with session_factory() as guard_holder:

        async def after_queued(*args, **kwargs):
            count = await real_degrade(*args, **kwargs)
            queued.append(count)
            async with session_factory() as observer:
                assert await semantic_rows(observer, run_id) == []
                assert (await observer.get(CheckRun, run_id)).stage_status is None
            if loss == "replacement":
                async with session_factory() as replacement:
                    row = await replacement.get(CheckRun, run_id)
                    row.claimed_at = await replacement.scalar(select(func.clock_timestamp()))
                    await replacement.commit()
            elif loss == "busy_guard":
                assert await try_guard(guard_holder, settings)
            else:
                raise machine.ClaimLost
            return count

        monkeypatch.setattr(machine, "_degrade_pending_semantic", after_queued)
        async with session_factory() as pipeline:
            result = await machine.run_check_run(
                pipeline,
                await pipeline.get(CheckRun, run_id),
                settings,
                OutageClient(),
                claim_token=token,
            )
            assert result is False
        await guard_holder.rollback()
    assert queued == [2]
    async with session_factory() as observer:
        assert await semantic_rows(observer, run_id) == []
        run = await observer.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.semantic and run.stage_status is None
        assert not await observer.scalar(
            select(ReadinessReport.id).where(ReadinessReport.check_run_id == run_id)
        )


async def test_cancellation_winning_at_outage_does_not_create_placeholders(
    session_factory, tmp_path, monkeypatch
):
    from app.pipeline.service import cancel_check_run

    run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)

    async def request_cancel():
        async with session_factory() as instructor_session:
            run = await instructor_session.get(CheckRun, run_id)
            manuscript = await instructor_session.get(Manuscript, run.manuscript_id)
            await cancel_check_run(instructor_session, run.id, manuscript.instructor_id)

    async with session_factory() as session:
        await machine.run_check_run(
            session,
            await session.get(CheckRun, run_id),
            settings,
            OutageClient(before_outage=request_cancel),
        )
    async with session_factory() as observer:
        assert (await observer.get(CheckRun, run_id)).status == CheckRunStatus.cancelled
        assert await semantic_rows(observer, run_id) == []
        assert not await observer.scalar(
            select(ReadinessReport.id).where(ReadinessReport.check_run_id == run_id)
        )


async def test_cancellation_after_queue_before_owned_commit_retains_only_intermediate_work(
    session_factory, tmp_path, monkeypatch
):
    from app.pipeline.service import cancel_check_run

    run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)
    real_degrade = machine._degrade_pending_semantic
    queued = []

    async def cancel_after_queue(*args, **kwargs):
        count = await real_degrade(*args, **kwargs)
        async with session_factory() as instructor_session:
            assert await semantic_rows(instructor_session, run_id) == []
            run = await instructor_session.get(CheckRun, run_id)
            manuscript = await instructor_session.get(Manuscript, run.manuscript_id)
            await cancel_check_run(instructor_session, run.id, manuscript.instructor_id)
        queued.append(count)
        return count

    monkeypatch.setattr(machine, "_degrade_pending_semantic", cancel_after_queue)
    async with session_factory() as session:
        await machine.run_check_run(
            session, await session.get(CheckRun, run_id), settings, OutageClient()
        )
    assert queued == [2]
    async with session_factory() as observer:
        run = await observer.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.cancelled
        assert run.stage_status["stages"]["semantic"]["degraded_count"] == 2
        assert run.stage_status["stages"]["semantic"]["degraded_code"] == "api_down"
        rows = await semantic_rows(observer, run_id)
        assert len(rows) == 2 and all(row.outcome == ResultOutcome.api_down for row in rows)
        assert not await observer.scalar(
            select(ReadinessReport.id).where(ReadinessReport.check_run_id == run_id)
        )


@pytest.mark.parametrize("commit", [True, False])
async def test_ungraded_producer_default_and_deferred_visibility(
    session_factory, tmp_path, monkeypatch, commit
):
    run_id, criteria, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as producer:
        criterion = await producer.get(Criterion, criteria[1])
        kwargs = {} if commit else {"commit": False}
        rows = await record_ungraded(
            producer,
            run_id,
            [criterion],
            outcome=ResultOutcome.api_down,
            reason="Controlled safe reason",
            **kwargs,
        )
        assert len(rows) == 1
        async with session_factory() as observer:
            visible = await semantic_rows(observer, run_id)
            assert len(visible) == int(commit)
        if not commit:
            assert rows[0].id is None
            await producer.rollback()
    async with session_factory() as observer:
        assert len(await semantic_rows(observer, run_id)) == int(commit)


async def test_outage_raised_after_all_results_saved_does_not_invent_missing_count(
    session_factory, tmp_path, monkeypatch
):
    run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)
    settings = settings.model_copy(update={"semantic_batch_max_criteria": 1})
    real_semantic = machine.run_semantic_checks_with_consistency
    saved = []

    async def saved_then_outage(*args, **kwargs):
        await real_semantic(*args, **kwargs)
        async with session_factory() as observer:
            saved.extend(snapshot(row) for row in await semantic_rows(observer, run_id))
        raise ApiDownError("Controlled terminal availability after a completed batch")

    monkeypatch.setattr(machine, "run_semantic_checks_with_consistency", saved_then_outage)
    async with session_factory() as session:
        await machine.run_check_run(
            session,
            await session.get(CheckRun, run_id),
            settings,
            _FlakyThenFineLLM(fail_after=100),
        )
    async with session_factory() as observer:
        assert len(saved) == 2
        assert [snapshot(row) for row in await semantic_rows(observer, run_id)] == saved
        run = await observer.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.done
        assert run.stage_status["stages"]["semantic"] == {"status": "done", "n_criteria": 2}
