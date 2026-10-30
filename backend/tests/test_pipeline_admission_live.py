"""F3 admission: distinct queued runs share the same database execution slot."""

import asyncio
import os
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.checks.router import apply_routing
from app.config import Settings, get_settings
from app.db import sqlalchemy_url
from app.errors import FileMalformedError
from app.llm.fake import FakeLLMClient
from app.models.audit import AuditLog
from app.models.enums import CheckRunStatus, LLMMode
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun, ReadinessReport
from app.pipeline import machine, worker
from app.pipeline.coordination import AdmissionState, admit_next
from app.pipeline.guards import try_guard
from app.pipeline.lease import ClaimLease
from app.pipeline.machine import ClaimLost, _transition_after_boundary

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
SCRATCH_DB = "veridical_admissiontest"


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
async def session_factory(scratch_url):
    engine = create_async_engine(sqlalchemy_url(scratch_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(text("TRUNCATE audit_log, instructor RESTART IDENTITY CASCADE"))
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


async def seed_queue(factory, labels=("A", "B")):
    async with factory() as session:
        run_ids = []
        for label in labels:
            owner = Instructor(email=f"admission-{label}@test.local", display_name=label)
            session.add(owner)
            await session.flush()
            manuscript = Manuscript(instructor_id=owner.id, group_label=label, file_ref="test.pdf")
            rubric = Rubric(instructor_id=owner.id, title="Format", source_file="format.pdf")
            session.add_all([manuscript, rubric])
            await session.flush()
            run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id, llm_mode=LLMMode.fake)
            session.add(run)
            await session.flush()
            run_ids.append(run.id)
        await session.commit()
        return run_ids


async def test_distinct_route_kicks_cannot_enter_concurrently(session_factory, monkeypatch):
    first_id, second_id = await seed_queue(session_factory)
    entered = asyncio.Event()
    second_entered = asyncio.Event()
    release = asyncio.Event()
    entries = []

    async def held_pipeline(session, run, settings, heartbeat=None, **_kwargs):
        run.status = CheckRunStatus.semantic
        run.started_at = datetime.now(UTC)
        await session.commit()
        entries.append(run.id)
        (entered if len(entries) == 1 else second_entered).set()
        await release.wait()
        run.status = CheckRunStatus.done
        await session.commit()

    monkeypatch.setattr(worker, "run_check_run", held_pipeline)
    first = asyncio.create_task(worker.advance_once(first_id, session_factory, get_settings()))
    second = None
    observed_second = asyncio.create_task(second_entered.wait())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        second = asyncio.create_task(
            worker.advance_once(second_id, session_factory, get_settings())
        )
        done, _ = await asyncio.wait(
            {second, observed_second}, timeout=5, return_when=asyncio.FIRST_COMPLETED
        )
        assert done, "The competing admission did not complete or reach its callback"
        assert entries == [first_id]
    finally:
        release.set()
        await asyncio.wait_for(first, 5)
        if second is not None:
            await asyncio.wait_for(second, 5)
        observed_second.cancel()
        await asyncio.gather(observed_second, return_exceptions=True)
    assert await worker.worker_tick(session_factory, get_settings()) is True
    assert entries == [first_id, second_id]


def fast_settings():
    # Short enough to keep the module quick, long enough that an ordinary
    # slow runner (event-loop stalls of a few hundred ms were measured with
    # the test process held to a quarter of a CPU) does not time out the
    # setup admissions. Stall tolerance has its own tests below.
    return Settings(
        _env_file=None,
        veridical_fake_llm=True,
        pipeline_claim_stale_seconds=1.0,
        pipeline_claim_renew_seconds=0.05,
        pipeline_coordination_timeout_seconds=0.4,
        pipeline_worker_poll_seconds=0.025,
        pipeline_run_deadline_seconds=5,
    )


async def test_uninterrupted_module_renews_without_committing_its_pending_work(
    session_factory, monkeypatch
):
    first_id, _ = await seed_queue(session_factory)
    settings = fast_settings()
    entered, release = asyncio.Event(), asyncio.Event()
    entries = []

    async def held(session, run, settings, heartbeat=None, **_kwargs):
        entries.append(run.id)
        session.add(
            AuditLog(
                event_type="uat_pending_work",
                check_run_id=run.id,
                payload={"fake_llm": True, "synthetic_fixture": True},
            )
        )
        entered.set()
        await release.wait()  # No stage heartbeat during this entire await.
        if heartbeat is not None:
            await heartbeat()
        run.status = CheckRunStatus.done
        await session.commit()

    monkeypatch.setattr(worker, "run_check_run", held)
    task = asyncio.create_task(worker.advance_once(first_id, session_factory, settings))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with session_factory() as observer:
            initial = await observer.scalar(
                select(CheckRun.claimed_at).where(CheckRun.id == first_id)
            )
        await asyncio.sleep(settings.pipeline_claim_stale_seconds + 0.15)
        async with session_factory() as observer:
            renewed = await observer.scalar(
                select(CheckRun.claimed_at).where(CheckRun.id == first_id)
            )
            assert renewed > initial
            assert await observer.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert (
            await asyncio.wait_for(worker.worker_step(session_factory, settings), 2)
            == AdmissionState.busy
        )
        assert entries == [first_id]
    finally:
        release.set()
        await asyncio.wait_for(task, 5)
    async with session_factory() as observer:
        assert await observer.scalar(select(func.count()).select_from(AuditLog)) == 1
        assert (
            await observer.scalar(select(CheckRun.claimed_at).where(CheckRun.id == first_id))
            is None
        )


class StallOnCommit:
    """Session factory whose next commit blocks the event loop once.

    The block is scheduled with call_soon just before the commit is sent, so
    it runs while the commit's reply is in flight: the server commits, and the
    reply and the coordination timeout are both due when the loop resumes.
    That is what a slow host does to a renewal (a stop-the-world pause, a
    GIL-bound import in a worker thread)."""

    def __init__(self, factory, seconds):
        self.factory, self.seconds = factory, seconds
        self.armed = False
        self.resumed = asyncio.Event()
        self.resumed_at = None

    def __call__(self):
        return _StallSession(self)

    def _block(self):
        time.sleep(self.seconds)
        self.resumed_at = time.monotonic()
        self.resumed.set()


class _StallSession:
    def __init__(self, owner):
        self.owner = owner

    async def __aenter__(self):
        self.real = self.owner.factory()
        await self.real.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.real.__aexit__(*args)

    def __getattr__(self, name):
        return getattr(self.real, name)

    async def commit(self):
        if self.owner.armed:
            self.owner.armed = False
            # Flush first so the block lands on the COMMIT round trip itself.
            await self.real.flush()
            asyncio.get_running_loop().call_soon(self.owner._block)
        await self.real.commit()


def stall_settings(stale):
    return fast_settings().model_copy(
        update={
            "pipeline_claim_stale_seconds": stale,
            "pipeline_claim_renew_seconds": 0.1,
            "pipeline_coordination_timeout_seconds": 0.3,
        }
    )


async def test_a_loop_stall_during_a_renewal_commit_keeps_the_claim(
    session_factory, monkeypatch, caplog
):
    """A 2 s stall while a renewal's commit is in flight: the commit lands but
    its reply arrives after the coordination timeout. The lease must recognise
    its own new token instead of declaring the claim lost (which stopped
    renewal, let the claim go stale and let a second admission start the same
    run while the first was still in it)."""
    first_id, _ = await seed_queue(session_factory)
    settings = stall_settings(stale=3.0)
    stall = StallOnCommit(session_factory, 2.0)
    leases, entries = [], []
    real_lease = worker.ClaimLease

    def capture(*args, **kwargs):
        leases.append(real_lease(*args, **kwargs))
        return leases[-1]

    entered, release = asyncio.Event(), asyncio.Event()

    async def held(session, run, settings, heartbeat=None, **_kwargs):
        entries.append(run.id)
        entered.set()
        await release.wait()
        await heartbeat()
        run.status = CheckRunStatus.done
        await session.commit()

    monkeypatch.setattr(worker, "ClaimLease", capture)
    monkeypatch.setattr(worker, "run_check_run", held)
    task = asyncio.create_task(worker.advance_once(first_id, stall, settings))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        (lease,) = leases
        stall.armed = True
        await asyncio.wait_for(stall.resumed.wait(), 5)

        state = await asyncio.wait_for(worker.worker_step(session_factory, settings), 2)
        assert state == AdmissionState.busy
        assert time.monotonic() - stall.resumed_at < 0.5
        assert entries == [first_id]
        assert "Claim renewal deferred" in caplog.text, "the stall must cut off a renewal reply"

        async with session_factory() as observer:
            after_stall = await observer.scalar(
                select(CheckRun.claimed_at).where(CheckRun.id == first_id)
            )
        deadline = time.monotonic() + 2
        renewed = after_stall
        while renewed == after_stall and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            async with session_factory() as observer:
                renewed = await observer.scalar(
                    select(CheckRun.claimed_at).where(CheckRun.id == first_id)
                )
        assert renewed > after_stall, "renewal must continue after the stall"
        assert not lease.lost
    finally:
        release.set()
        await asyncio.wait_for(task, 5)
    assert entries == [first_id]
    async with session_factory() as observer:
        run = await observer.get(CheckRun, first_id)
        assert run.status == CheckRunStatus.done and run.claimed_at is None


async def test_a_stall_longer_than_the_claim_fails_the_holder_without_hanging(
    session_factory, monkeypatch
):
    """With a 1 s claim, a 2 s stall really does end ownership: the holder
    must find out at its next checkpoint, renewal must not revive the stale
    claim, and its release must still clear the row."""
    first_id, _ = await seed_queue(session_factory)
    settings = stall_settings(stale=1.0)
    stall = StallOnCommit(session_factory, 2.0)
    leases = []
    real_lease = worker.ClaimLease

    def capture(*args, **kwargs):
        leases.append(real_lease(*args, **kwargs))
        return leases[-1]

    entered, release = asyncio.Event(), asyncio.Event()
    outcome = {}

    async def held(session, run, settings, heartbeat=None, **_kwargs):
        entered.set()
        await release.wait()
        try:
            await heartbeat()
        except ClaimLost:
            outcome["lost"] = True
            await session.rollback()
            return False
        outcome["lost"] = False

    monkeypatch.setattr(worker, "ClaimLease", capture)
    monkeypatch.setattr(worker, "run_check_run", held)
    task = asyncio.create_task(worker.advance_once(first_id, stall, settings))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        (lease,) = leases
        stall.armed = True
        await asyncio.wait_for(stall.resumed.wait(), 5)
        async with session_factory() as observer:
            stored = await observer.scalar(
                select(CheckRun.claimed_at).where(CheckRun.id == first_id)
            )
        await asyncio.sleep(0.3)
        assert lease.lost
        async with session_factory() as observer:
            assert (
                await observer.scalar(select(CheckRun.claimed_at).where(CheckRun.id == first_id))
                == stored
            ), "renewal must not revive a claim that went stale"
    finally:
        release.set()
        await asyncio.wait_for(task, 5)
    assert outcome == {"lost": True}
    async with session_factory() as observer:
        run = await observer.get(CheckRun, first_id)
        assert run.claimed_at is None and run.status == CheckRunStatus.queued


def slow_renewal_settings():
    """Renewals a full second apart, so a test can act between the renewal
    whose reply was cut off and the next one (which would settle it)."""
    return fast_settings().model_copy(
        update={
            "pipeline_claim_stale_seconds": 3.0,
            "pipeline_claim_renew_seconds": 1.0,
            "pipeline_coordination_timeout_seconds": 0.3,
        }
    )


async def lease_with_cut_off_renewal(session_factory, stall_seconds):
    """A started lease whose latest renewal committed but never got its reply."""
    (run_id,) = await seed_queue(session_factory, ("A",))
    settings = slow_renewal_settings()
    async with session_factory() as session:
        admission = await admit_next(session, settings)
    stall = StallOnCommit(session_factory, stall_seconds)
    lease = ClaimLease(stall, admission, settings)
    stall.armed = True
    lease.start()
    await asyncio.wait_for(stall.resumed.wait(), 5)
    async with session_factory() as observer:
        stored = await observer.scalar(select(CheckRun.claimed_at).where(CheckRun.id == run_id))
    return run_id, settings, lease, stored


async def test_a_lifecycle_boundary_after_a_cut_off_renewal_sees_its_own_claim(
    session_factory,
):
    run_id, settings, lease, stored = await lease_with_cut_off_renewal(session_factory, 1.0)
    try:
        assert stored != lease.token, "the renewal's commit must have landed unacknowledged"
        async with session_factory() as pipeline:
            await machine._assert_claim_owner(pipeline, run_id, settings, lease.current_token)
            await pipeline.rollback()
        assert lease.token == stored
    finally:
        await lease.stop()
        async with session_factory() as cleanup:
            await worker._release_claim(cleanup, run_id, *lease.tokens())


async def test_a_holder_stopping_after_a_cut_off_renewal_still_releases_its_claim(
    session_factory, monkeypatch
):
    first_id, _ = await seed_queue(session_factory)
    settings = slow_renewal_settings()
    stall = StallOnCommit(session_factory, 1.0)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(session, run, settings, **_kwargs):
        entered.set()
        await release.wait()  # returns without a checkpoint that would settle it

    monkeypatch.setattr(worker, "run_check_run", held)
    task = asyncio.create_task(worker.advance_once(first_id, stall, settings))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        stall.armed = True
        await asyncio.wait_for(stall.resumed.wait(), 5)
    finally:
        release.set()
        await asyncio.wait_for(task, 5)
    async with session_factory() as observer:
        assert (
            await observer.scalar(select(CheckRun.claimed_at).where(CheckRun.id == first_id))
            is None
        ), "a claim left behind would hold the slot for the whole stale window"


async def test_a_stall_past_the_last_confirmed_expiry_keeps_a_claim_the_database_extended(
    session_factory,
):
    """The stall outlasts the expiry known from the last acknowledged renewal
    but not the one written by the cut-off renewal; the database still shows
    the claim as fresh, so the holder must keep it."""
    run_id, settings, lease, stored = await lease_with_cut_off_renewal(session_factory, 2.5)
    try:
        await lease.checkpoint()
        assert not lease.lost and lease.token == stored
    finally:
        await lease.stop()
        async with session_factory() as cleanup:
            await worker._release_claim(cleanup, run_id, *lease.tokens())


async def test_admission_that_times_out_answers_busy_and_withdraws_its_claim(
    session_factory, monkeypatch
):
    """A stall while admission's claim commit is in flight: the claim lands
    but admission cannot know. It answers busy (no crash out of the poller)
    and takes the claim back, so the run is not held for the stale window."""
    (run_id,) = await seed_queue(session_factory, ("A",))
    settings = stall_settings(stale=3.0)
    stall = StallOnCommit(session_factory, 1.0)
    stall.armed = True

    async def forbidden(*_args, **_kwargs):
        pytest.fail("An unconfirmed admission must not run the check")

    monkeypatch.setattr(worker, "run_check_run", forbidden)
    state = await asyncio.wait_for(worker.worker_step(stall, settings), 5)
    assert state == AdmissionState.busy
    async with session_factory() as observer:
        assert (
            await observer.scalar(select(CheckRun.claimed_at).where(CheckRun.id == run_id)) is None
        )


async def test_terminal_callback_keeps_slot_until_final_release(session_factory, monkeypatch):
    first_id, _ = await seed_queue(session_factory)
    entered, release = asyncio.Event(), asyncio.Event()

    async def terminal_held(session, run, settings, **_kwargs):
        run.status = CheckRunStatus.done
        await session.commit()
        entered.set()
        await release.wait()

    monkeypatch.setattr(worker, "run_check_run", terminal_held)
    task = asyncio.create_task(worker.advance_once(first_id, session_factory, get_settings()))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert await worker.worker_step(session_factory, get_settings()) == AdmissionState.busy
    finally:
        release.set()
        await asyncio.wait_for(task, 5)


async def test_renewal_row_contention_releases_global_guard_and_recovers(session_factory):
    await seed_queue(session_factory, ("A",))
    settings = fast_settings()
    async with session_factory() as session:
        admission = await admit_next(session, settings)
    lease = ClaimLease(session_factory, admission, settings)
    original = lease.token
    async with session_factory() as pipeline:
        await pipeline.scalar(select(CheckRun).where(CheckRun.id == lease.run_id).with_for_update())
        await asyncio.wait_for(lease._renew(), settings.pipeline_coordination_timeout_seconds * 2)
        assert lease.token == original and not lease.lost
        async with session_factory() as other:
            assert await try_guard(other, settings) is True
            await other.rollback()
        await pipeline.rollback()
    await lease._renew()
    assert lease.token > original and not lease.lost
    async with session_factory() as cleanup:
        await worker._release_claim(cleanup, lease.run_id, lease.token)


async def test_expired_holder_cannot_renew_after_another_run_is_admitted(session_factory):
    first_id, second_id = await seed_queue(session_factory)
    settings = fast_settings()
    async with session_factory() as session:
        admission = await admit_next(session, settings)
    lease = ClaimLease(session_factory, admission, settings)
    async with session_factory() as session:
        first = await session.get(CheckRun, first_id)
        first.started_at = await session.scalar(select(func.clock_timestamp()))
        first.status = CheckRunStatus.semantic
        await session.commit()
    await asyncio.sleep(settings.pipeline_claim_stale_seconds + 0.05)
    async with session_factory() as session:
        replacement = await admit_next(session, settings)
    assert replacement.state == AdmissionState.admitted and replacement.run.id == second_id
    await lease._renew()
    assert lease.lost
    async with session_factory() as cleanup:
        await worker._release_claim(cleanup, first_id, lease.token)
        assert (
            await cleanup.scalar(select(CheckRun.claimed_at).where(CheckRun.id == second_id))
            == replacement.token
        )
        await worker._release_claim(cleanup, second_id, replacement.token)


async def test_dirty_stage_metadata_cannot_flush_after_claim_replacement(session_factory):
    (run_id,) = await seed_queue(session_factory, ("A",))
    async with session_factory() as setup:
        run = await setup.get(CheckRun, run_id)
        run.status = CheckRunStatus.aggregating
        run.claimed_at = await setup.scalar(select(func.clock_timestamp()))
        original = run.claimed_at
        await setup.commit()
    async with session_factory() as pipeline:
        run = await pipeline.get(CheckRun, run_id)
        run.stage_status = {"stages": {"aggregating": {"status": "done"}}}
        async with session_factory() as replacement:
            row = await replacement.get(CheckRun, run_id)
            row.claimed_at = await replacement.scalar(select(func.clock_timestamp()))
            await replacement.commit()

        async def token():
            return original

        with pytest.raises(ClaimLost):
            await _transition_after_boundary(
                pipeline, run, CheckRunStatus.aggregating, get_settings(), claim_token=token
            )
        await pipeline.rollback()
    async with session_factory() as observer:
        run = await observer.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.aggregating
        assert run.finished_at is None and run.stage_status is None


async def test_stop_waits_for_committed_renewal_token_publication(session_factory):
    await seed_queue(session_factory, ("A",))
    settings = fast_settings()
    async with session_factory() as session:
        admission = await admit_next(session, settings)
    committed, publish = asyncio.Event(), asyncio.Event()

    class HeldCommitSession:
        async def __aenter__(self):
            self.real = session_factory()
            await self.real.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.real.__aexit__(*args)

        def __getattr__(self, name):
            return getattr(self.real, name)

        async def commit(self):
            await self.real.commit()
            committed.set()
            await publish.wait()

    lease = ClaimLease(HeldCommitSession, admission, settings)
    original = lease.token
    lease.start()
    stop = snapshot = None
    try:
        await asyncio.wait_for(committed.wait(), 5)
        async with session_factory() as observer:
            stored = await observer.scalar(
                select(CheckRun.claimed_at).where(CheckRun.id == lease.run_id)
            )
        assert stored > original and lease.token == original
        stop = asyncio.create_task(lease.stop())
        snapshot = asyncio.create_task(lease.current_token())
        await asyncio.sleep(0)
        assert not stop.done() and not snapshot.done()
    finally:
        publish.set()
        if stop is None:
            await lease.stop()
        else:
            await asyncio.wait_for(stop, 5)
        if snapshot is not None:
            assert await asyncio.wait_for(snapshot, 5) == stored
    assert lease.token == stored
    async with session_factory() as cleanup:
        await worker._release_claim(cleanup, lease.run_id, lease.token)
        assert (
            await cleanup.scalar(select(CheckRun.claimed_at).where(CheckRun.id == lease.run_id))
            is None
        )


async def claimed_run(factory, *, status):
    (run_id,) = await seed_queue(factory, ("A",))
    async with factory() as session:
        run = await session.get(CheckRun, run_id)
        run.status = status
        run.claimed_at = await session.scalar(select(func.clock_timestamp()))
        token = run.claimed_at
        await session.commit()
    return run_id, token


async def replace_claim(factory, run_id):
    async with factory() as session:
        run = await session.get(CheckRun, run_id)
        run.claimed_at = await session.scalar(select(func.clock_timestamp()))
        token = run.claimed_at
        await session.commit()
        return token


async def test_loss_during_aggregation_cannot_publish_done(session_factory, monkeypatch):
    run_id, original = await claimed_run(session_factory, status=CheckRunStatus.aggregating)
    replacement = None

    async def aggregate(*_args, commit):
        assert commit is True
        nonlocal replacement
        replacement = await replace_claim(session_factory, run_id)

    async def token():
        return original

    monkeypatch.setattr(machine, "aggregate_and_score", aggregate)
    async with session_factory() as pipeline:
        run = await pipeline.get(CheckRun, run_id)
        await apply_routing(pipeline, run_id, [])
        assert (
            await machine.run_check_run(
                pipeline, run, get_settings(), FakeLLMClient(), claim_token=token
            )
            is False
        )
    assert replacement is not None
    async with session_factory() as observer:
        run = await observer.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.aggregating
        assert run.finished_at is None and run.stage_status is None
        assert run.claimed_at == replacement != original


@pytest.mark.parametrize("failure", ["blocked", "terminal", "malformed", "unexpected"])
async def test_old_exception_handler_cannot_overwrite_replacement(
    session_factory, monkeypatch, failure
):
    run_id, original = await claimed_run(session_factory, status=CheckRunStatus.ingesting)
    errors = {
        "blocked": machine.PipelineBlockedError(machine.StageBlock("api_down", "Synthetic", None)),
        "terminal": machine.TerminalFailure("synthetic", "Synthetic"),
        "malformed": FileMalformedError("Synthetic"),
        "unexpected": RuntimeError("Synthetic"),
    }
    replacement = None

    async def fail(*_args):
        nonlocal replacement
        replacement = await replace_claim(session_factory, run_id)
        raise errors[failure]

    async def token():
        return original

    monkeypatch.setattr(machine, "_run_ingesting_stage", fail)
    async with session_factory() as pipeline:
        run = await pipeline.get(CheckRun, run_id)
        assert (
            await machine.run_check_run(
                pipeline, run, get_settings(), FakeLLMClient(), claim_token=token
            )
            is False
        )
    async with session_factory() as observer:
        run = await observer.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.ingesting
        assert run.finished_at is None and run.stage_status is None
        assert run.claimed_at == replacement != original


async def test_expiry_during_cancellation_rolls_back_pending_report_and_audits(
    session_factory, monkeypatch
):
    settings = fast_settings()
    run_id, original = await claimed_run(session_factory, status=CheckRunStatus.integrity)
    async with session_factory() as setup:
        run = await setup.get(CheckRun, run_id)
        run.cancel_requested_at = datetime.now(UTC)
        setup.add(ReadinessReport(check_run_id=run_id, status="needs_review"))
        await setup.commit()

    async def cleanup(session, **_kwargs):
        session.add(
            AuditLog(
                event_type="uat_pending_cleanup", check_run_id=run_id, payload={"fake_llm": True}
            )
        )
        await asyncio.sleep(settings.pipeline_claim_stale_seconds + 0.05)
        return False

    async def token():
        return original

    monkeypatch.setattr(machine, "withdraw_manuscript_if_orphaned", cleanup)
    async with session_factory() as pipeline:
        run = await pipeline.get(CheckRun, run_id)
        assert (
            await machine.run_check_run(pipeline, run, settings, FakeLLMClient(), claim_token=token)
            is False
        )
    async with session_factory() as observer:
        run = await observer.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.integrity and run.cancel_requested_at is not None
        assert run.finished_at is None
        assert await observer.scalar(select(func.count()).select_from(ReadinessReport)) == 1
        assert await observer.scalar(select(func.count()).select_from(AuditLog)) == 0


async def test_busy_final_guard_yields_without_lifecycle_publication(session_factory):
    run_id, original = await claimed_run(session_factory, status=CheckRunStatus.aggregating)

    async def token():
        return original

    async with session_factory() as holder, session_factory() as pipeline:
        assert await try_guard(holder, get_settings())
        run = await pipeline.get(CheckRun, run_id)
        assert (
            await asyncio.wait_for(
                machine.run_check_run(
                    pipeline, run, get_settings(), FakeLLMClient(), claim_token=token
                ),
                2,
            )
            is False
        )
        await holder.rollback()
    async with session_factory() as observer:
        run = await observer.get(CheckRun, run_id)
        assert run.status == CheckRunStatus.aggregating and run.stage_status is None


async def test_cancellation_after_admission_before_execution_releases_claim(
    session_factory, monkeypatch
):
    (run_id,) = await seed_queue(session_factory, ("A",))
    closing = asyncio.Event()
    calls = 0

    class DelayedFirstClose:
        async def __aenter__(self):
            nonlocal calls
            self.index = calls
            calls += 1
            self.real = session_factory()
            await self.real.__aenter__()
            return self

        async def __aexit__(self, *args):
            try:
                if self.index == 0:
                    closing.set()
                    await asyncio.Event().wait()
            finally:
                await self.real.__aexit__(*args)

        def __getattr__(self, name):
            return getattr(self.real, name)

    async def forbidden(*_args, **_kwargs):
        pytest.fail("The cancelled admission must not enter execution")

    monkeypatch.setattr(worker, "run_check_run", forbidden)
    task = asyncio.create_task(worker.worker_step(DelayedFirstClose, get_settings()))
    try:
        await asyncio.wait_for(closing.wait(), 5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    async with session_factory() as observer:
        assert (
            await observer.scalar(select(CheckRun.claimed_at).where(CheckRun.id == run_id)) is None
        )


async def test_requested_run_filter_preserves_fair_turn_and_other_runs(
    session_factory, monkeypatch
):
    first_id, second_id = await seed_queue(session_factory)
    entries = []

    async def finish(session, run, settings, **_kwargs):
        entries.append((run.id, settings.llm_cache_bypass))
        run.status = CheckRunStatus.done
        run.started_at = datetime.now(UTC)
        await session.commit()

    monkeypatch.setattr(worker, "run_check_run", finish)
    probe_settings = get_settings().model_copy(update={"llm_cache_bypass": True})
    state = await worker.worker_step(session_factory, probe_settings, requested_run_id=second_id)
    assert state == AdmissionState.busy and entries == []
    async with session_factory() as observer:
        assert (
            await observer.scalar(
                select(func.count()).select_from(CheckRun).where(CheckRun.claimed_at.is_not(None))
            )
            == 0
        )
    assert await worker.worker_tick(session_factory, get_settings()) is True
    assert entries == [(first_id, False)]
    assert (
        await worker.worker_step(session_factory, probe_settings, requested_run_id=second_id)
        == AdmissionState.admitted
    )
    assert entries == [(first_id, False), (second_id, True)]


async def test_renewal_stops_at_configured_deadline_and_checkpoint_reports_limit(session_factory):
    await seed_queue(session_factory, ("A",))
    settings = fast_settings().model_copy(update={"pipeline_run_deadline_seconds": 0.15})
    async with session_factory() as session:
        admission = await admit_next(session, settings)
    lease = ClaimLease(session_factory, admission, settings)
    lease.start()
    try:
        await asyncio.sleep(0.25)
        assert lease.task.done()
        stopped_token = lease.token
        with pytest.raises(machine.TerminalFailure, match="processing time limit"):
            await lease.checkpoint()
        await asyncio.sleep(0.1)
        assert lease.token == stopped_token
    finally:
        await lease.stop()
        async with session_factory() as cleanup:
            await worker._release_claim(cleanup, lease.run_id, lease.token)
