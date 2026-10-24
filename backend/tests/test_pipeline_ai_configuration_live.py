"""F3/F8: known AI configuration limits publish an honest reviewable report."""

import asyncio
import copy
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import sqlalchemy_url
from app.errors import RuntimeApiKeyRejectedError
from app.llm.base import LLMNotConfiguredError
from app.models.enums import CheckKind, CheckRunStatus, ReadinessStatus, ResultOutcome
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion
from app.models.run import CheckResult, CheckRun, ReadinessReport
from app.pipeline import machine
from app.pipeline.guards import try_guard
from tests.test_pipeline_machine_live import _seed

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="integration: needs a live Postgres"
)


@pytest.fixture(scope="module")
def configuration_url():
    from alembic import command
    from tests.test_schema import _admin_execute, _alembic_config, _swap_db

    base = os.environ["DATABASE_URL"]
    name = f"veridical_config470_{uuid4().hex[:12]}"
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{name}"'))
    url = _swap_db(base, name)
    command.upgrade(_alembic_config(url), "head")
    try:
        yield url
    finally:
        asyncio.run(_admin_execute(base, f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture
async def session_factory(configuration_url):
    engine = create_async_engine(sqlalchemy_url(configuration_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        # Audit rows deliberately outlive cascade deletion. Keep sequences
        # monotonic so a new synthetic run cannot inherit another run's plan.
        await session.execute(text("TRUNCATE instructor CASCADE"))
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


async def rows(session, run_id):
    return list(
        await session.scalars(
            select(CheckResult)
            .where(CheckResult.check_run_id == run_id, CheckResult.criterion_id.is_not(None))
            .order_by(CheckResult.id)
        )
    )


def install_failure(monkeypatch, cause, *, before_failure=None):
    calls = []

    class Rejected:
        async def complete(self, *args, **kwargs):
            calls.append("request")
            if before_failure:
                await before_failure()
            raise RuntimeApiKeyRejectedError("private controlled provider diagnostic")

    async def resolve(*args, **kwargs):
        calls.append("resolve")
        if cause == "ai_not_configured":
            if before_failure:
                await before_failure()
            raise LLMNotConfiguredError("private controlled constructor diagnostic")
        return Rejected()

    monkeypatch.setattr(machine, "get_llm_client_for", resolve)
    return calls


@pytest.mark.parametrize("cause", ["ai_not_configured", "ai_key_rejected"])
@pytest.mark.parametrize("quota_floor", [True, False])
async def test_configuration_floor_finishes_and_does_not_regrade_old_run(
    session_factory, tmp_path, monkeypatch, cause, quota_floor
):
    run_id, criteria, settings = await _seed(session_factory, tmp_path, monkeypatch)
    settings = settings.model_copy(update={"pipeline_degrade_on_quota": quota_floor})
    calls = install_failure(monkeypatch, cause)
    async with session_factory() as session:
        await machine.run_check_run(session, await session.get(CheckRun, run_id), settings)
    assert calls == (["resolve"] if cause == "ai_not_configured" else ["resolve", "request"])
    async with session_factory() as observer:
        run = await observer.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.done, run.stage_status
        assert run.stage_status["stages"]["semantic"] == {
            "status": "done",
            "n_criteria": 2,
            "degraded_count": 2,
            "degraded_code": cause,
        }
        saved = await rows(observer, run_id)
        assert {row.criterion_id for row in saved} == set(criteria)
        assert saved[0].kind == CheckKind.structural and saved[0].outcome == ResultOutcome.passed
        for row in saved[1:]:
            assert row.outcome == ResultOutcome.unverifiable and row.score is None
            assert row.detail == {
                "basis": "not-graded",
                "ungraded_cause": cause,
                "reason": getattr(settings, f"pipeline_{cause}_degraded_reason"),
            }
        snapshot = [(r.id, r.outcome, r.score, copy.deepcopy(r.detail)) for r in saved]
        report = await observer.scalar(
            select(ReadinessReport).where(ReadinessReport.check_run_id == run_id)
        )
        assert report is not None and report.status == ReadinessStatus.needs_review
    monkeypatch.setattr(machine, "get_llm_client_for", lambda *a, **k: pytest.fail("No refill"))
    async with session_factory() as session:
        await machine.run_check_run(session, await session.get(CheckRun, run_id), settings)
    async with session_factory() as observer:
        assert [
            (r.id, r.outcome, r.score, r.detail) for r in await rows(observer, run_id)
        ] == snapshot


async def test_structural_and_zero_candidate_work_needs_no_client(
    session_factory, tmp_path, monkeypatch
):
    run_id, criteria, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        for criterion_id in criteria[1:]:
            await session.delete(await session.get(Criterion, criterion_id))
        await session.commit()
    calls = install_failure(monkeypatch, "ai_not_configured")
    async with session_factory() as session:
        await machine.run_check_run(session, await session.get(CheckRun, run_id), settings)
    assert calls == []
    async with session_factory() as observer:
        run = await observer.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.done, run.stage_status
        assert "degraded_code" not in run.stage_status["stages"]["semantic"]


async def test_unexpected_constructor_failure_is_terminal_not_configuration_floor(
    session_factory, tmp_path, monkeypatch
):
    run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)

    async def broken(*args, **kwargs):
        raise RuntimeError("private unknown constructor failure")

    monkeypatch.setattr(machine, "get_llm_client_for", broken)
    async with session_factory() as session:
        await machine.run_check_run(session, await session.get(CheckRun, run_id), settings)
    async with session_factory() as observer:
        run = await observer.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.failed
        assert run.stage_status["failed"]["code"] == "unexpected_error"
        assert len(await rows(observer, run_id)) == 1
        assert not await observer.scalar(
            select(ReadinessReport.id).where(ReadinessReport.check_run_id == run_id)
        )


@pytest.mark.parametrize("loss", ["replacement", "busy_guard", "control_flow_loss"])
async def test_unowned_configuration_rows_and_metadata_roll_back(
    session_factory, tmp_path, monkeypatch, loss
):
    run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as setup:
        run = await setup.get(CheckRun, run_id)
        run.claimed_at = await setup.scalar(select(func.clock_timestamp()))
        original = run.claimed_at
        await setup.commit()

    async def token():
        return original

    install_failure(monkeypatch, "ai_not_configured")
    real_degrade = machine._degrade_pending_semantic
    queued = []
    async with session_factory() as guard_holder:

        async def after_queued(*args, **kwargs):
            count = await real_degrade(*args, **kwargs)
            assert kwargs["commit"] is False
            queued.append(count)
            async with session_factory() as observer:
                assert len(await rows(observer, run_id)) == 1
                assert (
                    "semantic" not in (await observer.get(CheckRun, run_id)).stage_status["stages"]
                )
            if loss == "replacement":
                async with session_factory() as replacement:
                    run = await replacement.get(CheckRun, run_id)
                    run.claimed_at = await replacement.scalar(select(func.clock_timestamp()))
                    await replacement.commit()
            elif loss == "busy_guard":
                assert await try_guard(guard_holder, settings)
            else:
                raise machine.ClaimLost
            return count

        monkeypatch.setattr(machine, "_degrade_pending_semantic", after_queued)
        async with session_factory() as pipeline:
            assert (
                await machine.run_check_run(
                    pipeline, await pipeline.get(CheckRun, run_id), settings, claim_token=token
                )
                is False
            )
        await guard_holder.rollback()
    assert queued == [2]
    async with session_factory() as observer:
        assert len(await rows(observer, run_id)) == 1
        run = await observer.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.semantic
        assert "semantic" not in run.stage_status["stages"]


async def test_cancellation_wins_before_configuration_placeholders(
    session_factory, tmp_path, monkeypatch
):
    from app.pipeline.service import cancel_check_run

    run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)

    async def cancel():
        async with session_factory() as session:
            run = await session.get(CheckRun, run_id)
            manuscript = await session.get(Manuscript, run.manuscript_id)
            await cancel_check_run(session, run_id, manuscript.instructor_id)

    install_failure(monkeypatch, "ai_not_configured", before_failure=cancel)
    async with session_factory() as session:
        await machine.run_check_run(session, await session.get(CheckRun, run_id), settings)
    async with session_factory() as observer:
        assert (await observer.get(CheckRun, run_id)).status == CheckRunStatus.cancelled
        assert len(await rows(observer, run_id)) == 1
