"""BUG-144 regression: `advance_once` (route-kicked) and `worker_loop`'s
poll must never both be inside `run_check_run` for the SAME check_run at
once. The ticket's own test-coverage finding: starting both drivers from
`queued` is NOT a real reproduction (the second bails at the first
conditional stage transition) — the bug only appears when the second
driver arrives MID-STAGE, while `run_check_run` is still doing real work.
These tests simulate that arrival directly (a slow, instrumented stub in
place of the real stage machinery) rather than depending on the full
pipeline's own timing, which live LLM/DB latency can't reproduce
deterministically."""

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

import app.pipeline.machine as pipeline_machine
from app.checks.router import apply_routing
from app.config import get_settings
from app.llm.fake import FakeLLMClient
from app.models.enums import CheckRunStatus
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun
from app.pipeline import worker
from app.pipeline.machine import ClaimLost, run_check_run

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_workerclaimtest"


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
async def _clean(session_factory):
    async with session_factory() as session:
        await session.execute(
            text("TRUNCATE check_run, rubric, manuscript, instructor RESTART IDENTITY CASCADE")
        )
        await session.commit()
    yield


async def _seed_check_run(session_factory) -> int:
    async with session_factory() as session:
        instructor = Instructor(
            email=f"worker-claim-test-{time.time_ns()}@test.local", display_name="Worker Test"
        )
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(
            instructor_id=instructor.id, group_label="Group A", file_ref="test.pdf"
        )
        session.add(manuscript)
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add(rubric)
        await session.commit()
        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        await session.commit()
        return check_run.id


async def test_duplicate_start_does_not_advance_blocked_run_before_resume(
    session_factory, monkeypatch
):
    run_id = await _seed_check_run(session_factory)
    async with session_factory() as session:
        run = await session.get(CheckRun, run_id)
        run.stage_status = {
            "blocked": {
                "reason": "api_down",
                "resume_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
        }
        await session.commit()

    async def forbidden(*args, **kwargs):
        pytest.fail("a repeated Start cannot bypass the recorded resume time")

    monkeypatch.setattr(worker, "run_check_run", forbidden)
    await worker.advance_once(run_id, session_factory, get_settings())
    async with session_factory() as session:
        run = await session.get(CheckRun, run_id)
        assert run.claimed_at is None
        assert run.stage_status["blocked"]["reason"] == "api_down"


@pytest.mark.parametrize("route_first", [True, False])
async def test_advance_once_and_worker_tick_never_both_enter_the_same_run(
    session_factory, monkeypatch, route_first
):
    """Hold driver A mid-stage until driver B has attempted the real claim.

    A sleep does not establish overlap: opening B's connection may take longer
    than A's whole stage, in which case a second sequential entry is legitimate.
    """
    check_run_id = await _seed_check_run(session_factory)
    settings = get_settings()

    entries: list[int] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_run_check_run(session, check_run, settings, heartbeat=None, **_kwargs):
        entries.append(check_run.id)
        entered.set()
        await release.wait()

    monkeypatch.setattr(worker, "run_check_run", slow_run_check_run)

    async def drive(route):
        if route:
            await worker.advance_once(check_run_id, session_factory, settings)
        else:
            await worker.worker_tick(session_factory, settings)

    first = asyncio.create_task(drive(route_first))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        await asyncio.wait_for(drive(not route_first), timeout=10)
        assert entries == [check_run_id]
    finally:
        release.set()
        await asyncio.wait_for(first, timeout=10)

    # Without the claim, BOTH drivers would have entered -- this is the
    # literal assertion that would have failed pre-fix (2 entries, 4 LLM
    # calls in the ticket's own local reproduction shape).
    assert entries == [check_run_id]

    async with session_factory() as session:
        row = await session.scalar(select(CheckRun).where(CheckRun.id == check_run_id))
        assert row.claimed_at is None  # released after the run, not stuck


async def test_claim_is_released_so_a_later_stage_can_still_be_picked_up(
    session_factory, monkeypatch
):
    """A claim must not outlive the call it guards -- a SECOND, later
    advance (e.g. the next stage after a quota pause) must proceed
    normally once the first has finished."""
    check_run_id = await _seed_check_run(session_factory)
    settings = get_settings()
    entries: list[int] = []

    async def fast_run_check_run(session, check_run, settings, heartbeat=None, **_kwargs):
        entries.append(check_run.id)

    monkeypatch.setattr(worker, "run_check_run", fast_run_check_run)

    await worker.advance_once(check_run_id, session_factory, settings)
    await worker.advance_once(check_run_id, session_factory, settings)

    assert entries == [check_run_id, check_run_id]


async def test_stale_claim_is_reclaimable_after_a_simulated_crash(session_factory, monkeypatch):
    """A process that crashed mid-run must not strand its check_run
    claimed forever -- a claim older than `pipeline_claim_stale_seconds`
    is treated as abandoned."""
    check_run_id = await _seed_check_run(session_factory)
    settings = get_settings()

    async with session_factory() as session:
        # Claim expiry uses the database clock, independent of client clock skew.
        stale_moment = await session.scalar(select(func.clock_timestamp())) - timedelta(
            seconds=settings.pipeline_claim_stale_seconds + 60
        )
        check_run = await session.get(CheckRun, check_run_id)
        check_run.claimed_at = stale_moment
        await session.commit()

    entries: list[int] = []

    async def fast_run_check_run(session, check_run, settings, heartbeat=None, **_kwargs):
        entries.append(check_run.id)

    monkeypatch.setattr(worker, "run_check_run", fast_run_check_run)

    await worker.advance_once(check_run_id, session_factory, settings)

    assert entries == [check_run_id]  # reclaimed despite the stale claimed_at


async def test_a_fresh_claim_is_not_reclaimable(session_factory, monkeypatch):
    """The mirror case of the stale-claim test above -- a RECENT claim
    (well within `pipeline_claim_stale_seconds`) must block a second
    driver, not just an old one."""
    check_run_id = await _seed_check_run(session_factory)
    settings = get_settings()

    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        check_run.claimed_at = datetime.now(UTC)
        await session.commit()

    entries: list[int] = []

    async def fast_run_check_run(session, check_run, settings, heartbeat=None, **_kwargs):
        entries.append(check_run.id)

    monkeypatch.setattr(worker, "run_check_run", fast_run_check_run)

    await worker.advance_once(check_run_id, session_factory, settings)

    assert entries == []  # never entered -- the run was already (freshly) claimed


async def test_heartbeat_keeps_a_slow_but_alive_run_from_going_stale(session_factory, monkeypatch):
    """backend review finding (BUG-144 review): a static
    `pipeline_claim_stale_seconds` timeout risks reintroducing double
    execution for a genuinely-alive-but-slow run (many criteria, LLM
    retries) -- exactly the degraded conditions where it matters most.
    `run_check_run`'s `heartbeat` callback (called from the same
    per-batch `cancellation_boundary` checkpoints every LLM-bound stage
    already uses) must keep the claim fresh as real progress happens, so
    a claim set BEFORE `pipeline_claim_stale_seconds` ago is still NOT
    reclaimable if it was heartbeat-refreshed since."""
    check_run_id = await _seed_check_run(session_factory)
    settings = get_settings()

    # Claim set well in the past -- would be stale on its own.
    async with session_factory() as session:
        # Claim expiry uses the database clock, independent of client clock skew.
        stale_moment = await session.scalar(select(func.clock_timestamp())) - timedelta(
            seconds=settings.pipeline_claim_stale_seconds + 60
        )
        check_run = await session.get(CheckRun, check_run_id)
        check_run.claimed_at = stale_moment
        await session.commit()

    heartbeats_fired = 0

    async def long_running_but_heartbeating(
        session, check_run, settings, heartbeat=None, **_kwargs
    ):
        nonlocal heartbeats_fired
        # Simulates a real stage calling `cancellation_boundary()` (and
        # therefore `heartbeat()`) after each of several batches -- the
        # FIRST call must succeed even though the ORIGINAL claim (set
        # above) is already stale, because `advance_once` re-claims fresh
        # before this stub ever runs.
        for _ in range(3):
            if heartbeat is not None:
                await heartbeat()
                heartbeats_fired += 1

    monkeypatch.setattr(worker, "run_check_run", long_running_but_heartbeating)

    await worker.advance_once(check_run_id, session_factory, settings)

    assert heartbeats_fired == 3
    async with session_factory() as session:
        row = await session.scalar(select(CheckRun).where(CheckRun.id == check_run_id))
        assert row.claimed_at is None  # released cleanly at the end


async def test_a_stolen_claims_original_holder_cannot_release_the_new_holders_claim(
    session_factory, monkeypatch
):
    """backend review finding (BUG-144 review, the more serious half): an
    UNCONDITIONAL release would let a stalled original holder's eventual
    `finally` wipe out a DIFFERENT process's legitimate re-claim, turning
    one double-execution into potentially unbounded re-entrancy. The
    fencing token must prevent this: once holder A's claim goes stale and
    holder B re-claims, A's own release (using its OLD token) must be a
    no-op -- B's claim survives."""
    check_run_id = await _seed_check_run(session_factory)
    settings = get_settings()

    # Holder A's claim, already stale.
    async with session_factory() as session:
        # Claim expiry uses the database clock, independent of client clock skew.
        stale_moment = await session.scalar(select(func.clock_timestamp())) - timedelta(
            seconds=settings.pipeline_claim_stale_seconds + 60
        )
        check_run = await session.get(CheckRun, check_run_id)
        check_run.claimed_at = stale_moment
        old_token = stale_moment
        await session.commit()

    # Holder B legitimately re-claims (simulating a second driver arriving
    # after A's process crashed).
    async with session_factory() as session:
        claimed = await worker.pick_next_runnable(session, settings)
        assert claimed is not None and claimed[0].id == check_run_id
        new_token = claimed[1]
    assert new_token is not None
    assert new_token != old_token

    # Holder A's belated cleanup, using its STALE token -- must be a no-op.
    async with session_factory() as session:
        await worker._release_claim(session, check_run_id, old_token)

    async with session_factory() as session:
        row = await session.scalar(select(CheckRun).where(CheckRun.id == check_run_id))
        # B's claim must have survived A's release attempt.
        assert row.claimed_at == new_token


async def _seed_two_instructor_queue(session_factory) -> dict[str, int]:
    """Instructor A queues three runs, all older than instructor B's one
    run -- the exact "defense morning" shape BUG-181 named: one account's
    backlog sitting entirely ahead of every other instructor's work in
    plain global FIFO."""
    async with session_factory() as session:
        instructor_a = Instructor(
            email=f"fairness-a-{time.time_ns()}@test.local", display_name="Instructor A"
        )
        instructor_b = Instructor(
            email=f"fairness-b-{time.time_ns()}@test.local", display_name="Instructor B"
        )
        session.add_all([instructor_a, instructor_b])
        await session.commit()

        manuscript_a = Manuscript(instructor_id=instructor_a.id, group_label="A", file_ref="a.pdf")
        manuscript_b = Manuscript(instructor_id=instructor_b.id, group_label="B", file_ref="b.pdf")
        session.add_all([manuscript_a, manuscript_b])
        rubric_a = Rubric(instructor_id=instructor_a.id, title="Format", source_file="ra.pdf")
        rubric_b = Rubric(instructor_id=instructor_b.id, title="Format", source_file="rb.pdf")
        session.add_all([rubric_a, rubric_b])
        await session.commit()

        base = datetime.now(UTC) - timedelta(minutes=10)
        a1 = CheckRun(manuscript_id=manuscript_a.id, rubric_id=rubric_a.id, created_at=base)
        a2 = CheckRun(
            manuscript_id=manuscript_a.id,
            rubric_id=rubric_a.id,
            created_at=base + timedelta(seconds=1),
        )
        a3 = CheckRun(
            manuscript_id=manuscript_a.id,
            rubric_id=rubric_a.id,
            created_at=base + timedelta(seconds=2),
        )
        # B's single run was queued AFTER all three of A's -- the plain-FIFO
        # failure case: B would sit dead last behind A's whole pile.
        b1 = CheckRun(
            manuscript_id=manuscript_b.id,
            rubric_id=rubric_b.id,
            created_at=base + timedelta(seconds=3),
        )
        session.add_all([a1, a2, a3, b1])
        await session.commit()
        return {"a1": a1.id, "a2": a2.id, "a3": a3.id, "b1": b1.id}


async def test_bug181_fair_queuing_interleaves_a_late_second_instructor(session_factory):
    """BUG-181: plain global FIFO let one account's backlog sit entirely
    ahead of every other instructor's work -- twenty queued runs from one
    account during defense season meant the whole cohort waited behind
    one person. `pick_next_runnable` must give instructor B's single
    (later-queued) run a turn right after instructor A's first pick, not
    push it behind A's whole remaining pile."""
    ids = await _seed_two_instructor_queue(session_factory)
    settings = get_settings()

    picked: list[int] = []
    async with session_factory() as session:
        for _ in range(4):
            result = await worker.pick_next_runnable(session, settings)
            assert result is not None
            run, _token = result
            picked.append(run.id)
            # A real run sets started_at when it begins -- that's the
            # signal fair queuing reads to know this instructor was just
            # served, so the simulation must set it too, not just mark
            # the run terminal.
            run.started_at = datetime.now(UTC)
            run.status = CheckRunStatus.done  # simulates completion, leaves the pool
            await session.commit()
            await worker._release_claim(session, run.id, _token)

    # Plain FIFO would have produced [a1, a2, a3, b1] -- B dead last behind
    # A's entire backlog. Fair queuing gives B a turn right after A's
    # first pick instead.
    assert picked == [ids["a1"], ids["b1"], ids["a2"], ids["a3"]]


async def test_bug181_three_instructors_rotate_instead_of_draining_one_at_a_time(
    session_factory,
):
    """backend review finding (BUG-181 review): the 2-instructor test above
    doesn't rule out degeneration at 3+ instructors specifically -- defense
    season means MANY concurrent instructors, not two. Three instructors
    each queue four runs, oldest-to-newest by instructor (A's whole block
    predates B's, which predates C's) -- the worst case for accidentally
    falling back to plain FIFO. Sustained fair queuing must rotate
    A,B,C,A,B,C,... across all 12 picks, never draining one instructor's
    backlog before another gets a second turn."""
    async with session_factory() as session:
        instructors = [
            Instructor(
                email=f"fairness-3way-{letter}-{time.time_ns()}@test.local", display_name=letter
            )
            for letter in ("A", "B", "C")
        ]
        session.add_all(instructors)
        await session.commit()

        manuscripts = [
            Manuscript(instructor_id=instructor.id, group_label=letter, file_ref=f"{letter}.pdf")
            for instructor, letter in zip(instructors, ("A", "B", "C"), strict=True)
        ]
        session.add_all(manuscripts)
        rubrics = [
            Rubric(instructor_id=instructor.id, title="Format", source_file=f"r{letter}.pdf")
            for instructor, letter in zip(instructors, ("A", "B", "C"), strict=True)
        ]
        session.add_all(rubrics)
        await session.commit()

        base = datetime.now(UTC) - timedelta(minutes=30)
        ids: dict[str, int] = {}
        runs = []
        tick = 0
        for letter, manuscript, rubric in zip(("A", "B", "C"), manuscripts, rubrics, strict=True):
            for n in range(4):
                run = CheckRun(
                    manuscript_id=manuscript.id,
                    rubric_id=rubric.id,
                    created_at=base + timedelta(seconds=tick),
                )
                tick += 1
                runs.append((f"{letter}{n + 1}", run))
        session.add_all(run for _label, run in runs)
        await session.commit()
        for label, run in runs:
            ids[label] = run.id

    settings = get_settings()
    picked: list[str] = []
    id_to_label = {run_id: label for label, run_id in ids.items()}
    async with session_factory() as session:
        for _ in range(12):
            result = await worker.pick_next_runnable(session, settings)
            assert result is not None
            run, _token = result
            picked.append(id_to_label[run.id])
            run.started_at = datetime.now(UTC)
            run.status = CheckRunStatus.done
            await session.commit()
            await worker._release_claim(session, run.id, _token)

    expected = ["A1", "B1", "C1", "A2", "B2", "C2", "A3", "B3", "C3", "A4", "B4", "C4"]
    assert picked == expected


async def test_bug181_in_progress_run_counts_as_this_instructors_last_turn(session_factory):
    """backend review finding (BUG-181 review): the "last served" signal
    must come from ANY of an instructor's check_run history, not just
    terminal ones -- an instructor whose only prior run is STILL running
    (non-terminal, `started_at` set) must not look like they've "never had
    a turn" just because nothing of theirs has finished yet. Instructor A
    has an in-progress run (older, already started) plus a freshly queued
    second run; instructor B has one run that has never started at all.
    B must go first -- A already has a real turn in flight."""
    async with session_factory() as session:
        instructor_a = Instructor(
            email=f"fairness-inprogress-a-{time.time_ns()}@test.local", display_name="A"
        )
        instructor_b = Instructor(
            email=f"fairness-inprogress-b-{time.time_ns()}@test.local", display_name="B"
        )
        session.add_all([instructor_a, instructor_b])
        await session.commit()

        manuscript_a = Manuscript(instructor_id=instructor_a.id, group_label="A", file_ref="a.pdf")
        manuscript_b = Manuscript(instructor_id=instructor_b.id, group_label="B", file_ref="b.pdf")
        session.add_all([manuscript_a, manuscript_b])
        rubric_a = Rubric(instructor_id=instructor_a.id, title="Format", source_file="ra.pdf")
        rubric_b = Rubric(instructor_id=instructor_b.id, title="Format", source_file="rb.pdf")
        session.add_all([rubric_a, rubric_b])
        await session.commit()

        base = datetime.now(UTC) - timedelta(minutes=10)
        a_in_progress = CheckRun(
            manuscript_id=manuscript_a.id,
            rubric_id=rubric_a.id,
            status=CheckRunStatus.semantic,
            created_at=base,
            started_at=base,
            claimed_at=datetime.now(UTC),  # actively held by another driver right now
        )
        a_queued = CheckRun(
            manuscript_id=manuscript_a.id,
            rubric_id=rubric_a.id,
            created_at=base + timedelta(minutes=2),
        )
        b_queued = CheckRun(
            manuscript_id=manuscript_b.id,
            rubric_id=rubric_b.id,
            created_at=base + timedelta(minutes=3),
        )
        session.add_all([a_in_progress, a_queued, b_queued])
        await session.commit()
        b_queued_id = b_queued.id

    settings = get_settings()
    async with session_factory() as session:
        assert await worker.pick_next_runnable(session, settings) is None
        await worker._release_claim(session, a_in_progress.id, a_in_progress.claimed_at)
        result = await worker.pick_next_runnable(session, settings)

    assert result is not None
    run, _token = result
    # The fresh global holder first reserved capacity. After release, B has
    # never had a turn at all; A's in-progress run means A HAS,
    # even though nothing of A's has finished yet -- B goes first.
    assert run.id == b_queued_id


async def test_bug181_fallback_to_global_fifo_when_every_head_is_blocked(session_factory):
    """When every instructor's OWN head run is blocked (PARKED, no known
    resume time), fair queuing must not stall the whole queue waiting on
    heads that can't run -- it must fall back to plain global FIFO among
    whatever else is left, across instructors, not just within one."""
    async with session_factory() as session:
        instructor_a = Instructor(
            email=f"fairness-blocked-a-{time.time_ns()}@test.local", display_name="A"
        )
        instructor_b = Instructor(
            email=f"fairness-blocked-b-{time.time_ns()}@test.local", display_name="B"
        )
        session.add_all([instructor_a, instructor_b])
        await session.commit()

        manuscript_a = Manuscript(instructor_id=instructor_a.id, group_label="A", file_ref="a.pdf")
        manuscript_b = Manuscript(instructor_id=instructor_b.id, group_label="B", file_ref="b.pdf")
        session.add_all([manuscript_a, manuscript_b])
        rubric_a = Rubric(instructor_id=instructor_a.id, title="Format", source_file="ra.pdf")
        rubric_b = Rubric(instructor_id=instructor_b.id, title="Format", source_file="rb.pdf")
        session.add_all([rubric_a, rubric_b])
        await session.commit()

        blocked = {"blocked": {"reason": "quota_exhausted"}}  # no resume_at -- blocked forever
        base = datetime.now(UTC) - timedelta(minutes=10)
        # Each instructor's HEAD (oldest run) is blocked; each also has a
        # second, unblocked run further back in the queue.
        a_head = CheckRun(
            manuscript_id=manuscript_a.id,
            rubric_id=rubric_a.id,
            created_at=base,
            stage_status=blocked,
        )
        b_head = CheckRun(
            manuscript_id=manuscript_b.id,
            rubric_id=rubric_b.id,
            created_at=base + timedelta(seconds=1),
            stage_status=blocked,
        )
        a_rest = CheckRun(
            manuscript_id=manuscript_a.id,
            rubric_id=rubric_a.id,
            created_at=base + timedelta(seconds=2),
        )
        b_rest = CheckRun(
            manuscript_id=manuscript_b.id,
            rubric_id=rubric_b.id,
            created_at=base + timedelta(seconds=3),
        )
        session.add_all([a_head, b_head, a_rest, b_rest])
        await session.commit()
        a_rest_id, b_rest_id = a_rest.id, b_rest.id

    settings = get_settings()
    async with session_factory() as session:
        result = await worker.pick_next_runnable(session, settings)

    assert result is not None
    run, _token = result
    # Both blocked heads were skipped; the oldest RUNNABLE run overall
    # (instructor A's second run) is what gets picked -- not stuck, and
    # not scoped to only one instructor's own remaining queue.
    assert run.id == a_rest_id
    assert run.id != b_rest_id


async def test_run_check_run_stops_cleanly_when_heartbeat_reports_claim_lost(
    session_factory, monkeypatch
):
    """backend review finding (BUG-144 follow-up review, empirically
    reproduced): a lost heartbeat used to be silently swallowed --
    `run_check_run` kept right on working with a now-invalid token, no
    signal that another driver had legitimately taken over. This drives
    the REAL `run_check_run` (not a stub), stubbing only the semantic
    stage's own internals to immediately call the `cancellation_boundary`
    it's handed -- exactly where a real stage's per-batch heartbeat fires
    -- with a `heartbeat` that raises `ClaimLost` on its very first call.
    `run_check_run` must catch it, return cleanly (no exception escapes to
    the caller), and leave the check_run's status untouched -- NOT marked
    `failed`, since nothing about the run actually failed."""
    async with session_factory() as session:
        instructor = Instructor(
            email=f"claimlost-test-{time.time_ns()}@test.local", display_name="X"
        )
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="t.pdf")
        session.add(manuscript)
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add(rubric)
        await session.commit()
        check_run = CheckRun(
            manuscript_id=manuscript.id,
            rubric_id=rubric.id,
            status=CheckRunStatus.semantic,
            started_at=datetime.now(UTC),
        )
        session.add(check_run)
        await session.commit()
        check_run_id = check_run.id

        await apply_routing(session, check_run_id, [])

    async def stub_semantic_stage(*args):
        # The real `_run_semantic_stage` calls `cancellation_boundary()`
        # (and therefore `heartbeat()`) once per batch -- this stub calls
        # the same last positional arg directly, standing in for "a real
        # batch just finished, checkpoint reached."
        await args[-1]()

    monkeypatch.setattr(pipeline_machine, "_run_semantic_stage", stub_semantic_stage)

    heartbeat_calls = 0

    async def lost_heartbeat() -> None:
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        raise ClaimLost

    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        # Must not raise -- ClaimLost is internal control flow, not a real
        # failure, and `run_check_run` owns catching it.
        assert (
            await run_check_run(
                session, check_run, get_settings(), llm=FakeLLMClient(), heartbeat=lost_heartbeat
            )
            is False
        )
    assert heartbeat_calls == 1

    async with session_factory() as session:
        row = await session.scalar(select(CheckRun).where(CheckRun.id == check_run_id))
        # Untouched -- still mid-stage, NOT marked failed (nothing failed;
        # another driver legitimately owns it now).
        assert row.status == CheckRunStatus.semantic
        assert row.stage_status is None or "failed" not in (row.stage_status or {})
