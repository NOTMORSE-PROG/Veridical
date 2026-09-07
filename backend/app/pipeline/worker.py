"""Background job runner (ticket research note: simplest option, no paid
queue infrastructure) — one check_run advances at a time, matching the
free dyno's single-worker reality (ENGINEERING §4). A poll loop is
started at app startup (`app.main`'s lifespan) for steady-state progress;
`advance_once` lets the create-check-run route kick a freshly queued run
immediately instead of waiting for the next poll tick.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import db
from app.config import Settings, get_settings
from app.models.enums import CheckRunStatus
from app.models.manuscript import Manuscript
from app.models.run import CheckRun
from app.pipeline.machine import ClaimLost, is_blocked, run_check_run

logger = logging.getLogger(__name__)

_NON_TERMINAL = (
    CheckRunStatus.queued,
    CheckRunStatus.ingesting,
    CheckRunStatus.structural,
    CheckRunStatus.semantic,
    CheckRunStatus.integrity,
    CheckRunStatus.aggregating,
)


async def _try_claim(
    session: AsyncSession, check_run_id: int, settings: Settings
) -> datetime | None:
    """BUG-144: atomic compare-and-swap claim -- the same conditional-UPDATE
    technique `_transition_after_boundary` (machine.py) already uses for
    stage transitions, at WHOLE-RUN scope this time (not just one stage),
    so `advance_once` and `worker_loop` can never both be inside
    `run_check_run` for the same check_run at once. A claim older than
    `settings.pipeline_claim_stale_seconds` is treated as abandoned (its
    holder crashed mid-run, or its heartbeat -- see `_heartbeat` below --
    genuinely stopped) and can be re-claimed.

    Returns the exact `claimed_at` value this call just wrote (or `None`
    if someone else already holds a fresh claim) -- the caller's FENCING
    TOKEN, threaded through `_heartbeat`/`_release_claim` so a claim that
    gets legitimately re-claimed out from under a stalled holder can never
    have its ORIGINAL holder's eventual cleanup silently steal it back
    (`backend-critic` finding, BUG-144 review: an unconditional release
    would let exactly that flip-flop happen)."""
    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=settings.pipeline_claim_stale_seconds)
    claimed = (
        await session.execute(
            update(CheckRun)
            .where(
                CheckRun.id == check_run_id,
                or_(CheckRun.claimed_at.is_(None), CheckRun.claimed_at < stale_before),
            )
            .values(claimed_at=now)
            .returning(CheckRun.claimed_at)
        )
    ).first()
    await session.commit()
    return claimed[0] if claimed is not None else None


async def _heartbeat(session: AsyncSession, check_run_id: int, token: datetime) -> datetime | None:
    """Refreshes the claim ONLY if `token` still matches what's stored --
    i.e. only if this caller still genuinely holds it. Returns the new
    token on success (the caller must use IT for the next heartbeat/
    release), or `None` if the claim was already reassigned (this
    holder's own heartbeat cadence fell behind `pipeline_claim_stale_seconds`
    and someone else legitimately reclaimed it) -- the caller should treat
    that as "stop, someone else owns this now", not retry."""
    now = datetime.now(UTC)
    refreshed = (
        await session.execute(
            update(CheckRun)
            .where(CheckRun.id == check_run_id, CheckRun.claimed_at == token)
            .values(claimed_at=now)
            .returning(CheckRun.claimed_at)
        )
    ).first()
    await session.commit()
    return refreshed[0] if refreshed is not None else None


async def _release_claim(session: AsyncSession, check_run_id: int, token: datetime) -> None:
    """Fencing-token-gated release -- clears the claim ONLY if `token`
    still matches, so a holder whose claim was already reassigned (see
    `_heartbeat`) can never wipe out the NEW legitimate holder's claim on
    its way out."""
    await session.execute(
        update(CheckRun)
        .where(CheckRun.id == check_run_id, CheckRun.claimed_at == token)
        .values(claimed_at=None)
    )
    await session.commit()


def _make_heartbeat(
    session: AsyncSession, check_run_id: int, token: datetime
) -> tuple[Callable[[], Awaitable[None]], Callable[[], datetime]]:
    """A single mutable cell (BUG-144) so the heartbeat closure passed into
    `run_check_run` and this call's eventual release both see the LATEST
    token after every successful refresh, not the original claim's. Returns
    `(heartbeat_callable, get_current_token)`.

    `backend-critic` finding (BUG-144 follow-up review, empirically
    reproduced): a lost claim (`_heartbeat` returns `None`) used to be a
    silent no-op here -- the caller kept using its now-stale token and
    `run_check_run` kept right on working, with no way to learn someone
    else now legitimately owns the run. `heartbeat()` now raises
    `ClaimLost`, which `run_check_run` handles the same way it already
    handles an accepted cancellation: stop immediately, touch nothing
    further, let the new legitimate holder own the rest."""
    state = {"token": token}

    async def heartbeat() -> None:
        refreshed = await _heartbeat(session, check_run_id, state["token"])
        if refreshed is None:
            raise ClaimLost
        state["token"] = refreshed

    return heartbeat, (lambda: state["token"])


async def pick_next_runnable(
    session: AsyncSession, settings: Settings | None = None
) -> tuple[CheckRun, datetime] | None:
    """Per-instructor fair queuing (BUG-181): plain global FIFO let one
    account's pile of queued runs sit ahead of every other instructor's
    work for as long as it took to drain — twenty runs from one account
    during defense season meant the whole cohort waited behind one person.

    Each instructor's OWN oldest non-terminal run (their queue's "head")
    is tried first; WHICH instructor's head goes first is decided by who
    was served longest ago — the most recent `started_at` across that
    instructor's entire history (any status, not just their current
    queue); never having had a run started outranks everyone who has.
    Ranking heads by their own `created_at` instead (the simpler thing to
    try) quietly degenerates back into plain FIFO: once an account's
    finished run is removed, their NEXT run is promoted to head and is
    just as "old" as before, so an account with an uninterrupted backlog
    would keep winning the "oldest head" comparison forever. Keying on
    when the instructor was last SERVED, not when their run was QUEUED, is
    what actually rotates turns across accounts.

    Only once every head is unrunnable (PARKED, or lost a claim race)
    does this fall back to plain global FIFO among what's left, so free
    capacity is never left idle for fairness's own sake — a solo
    instructor with several queued runs still gets them worked in order,
    back to back.

    BUG-144: still ATOMICALLY claims the run it returns (a conditional
    UPDATE, not just a SELECT), so two concurrent callers of this function
    can never both return the same run — returns `(run, claim_token)`;
    callers own releasing the claim (`_release_claim`, with the LATEST
    token if a heartbeat refreshed it) once `run_check_run` returns,
    success or failure."""
    settings = settings or get_settings()
    rows = (
        await session.execute(
            select(CheckRun, Manuscript.instructor_id)
            .join(Manuscript, CheckRun.manuscript_id == Manuscript.id)
            .where(CheckRun.status.in_(_NON_TERMINAL))
            .order_by(CheckRun.created_at)
        )
    ).all()
    if not rows:
        return None

    instructor_ids = {instructor_id for _run, instructor_id in rows}
    last_started_rows = await session.execute(
        select(Manuscript.instructor_id, func.max(CheckRun.started_at))
        .join(Manuscript, CheckRun.manuscript_id == Manuscript.id)
        .where(Manuscript.instructor_id.in_(instructor_ids))
        .group_by(Manuscript.instructor_id)
    )
    last_started: dict[int, datetime | None] = dict(last_started_rows.all())

    seen_instructors: set[int] = set()
    heads: list[tuple[CheckRun, int]] = []
    rest: list[CheckRun] = []
    for run, instructor_id in rows:
        if instructor_id in seen_instructors:
            rest.append(run)
        else:
            seen_instructors.add(instructor_id)
            heads.append((run, instructor_id))

    # Stable sort: every "never started" instructor gets the identical
    # sentinel key (comparing two `None`s with `<` would raise), so ties
    # there — including "nobody's started anything yet" — fall through
    # to `heads`'s existing created_at order instead.
    _never_started = datetime.min.replace(tzinfo=UTC)

    def _head_priority(item: tuple[CheckRun, int]) -> tuple[bool, datetime]:
        started = last_started.get(item[1])
        return (started is not None, started or _never_started)

    heads.sort(key=_head_priority)

    now = datetime.now(UTC)
    for run in (*(run for run, _instructor_id in heads), *rest):
        if is_blocked(run, now=now):
            continue
        token = await _try_claim(session, run.id, settings)
        if token is not None:
            await session.refresh(run)
            return run, token
    return None


async def advance_once(
    check_run_id: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
) -> None:
    """Advances ONE specific check_run to completion or its next block —
    used both by the create-route's "kick it now" call and by tests.

    BUG-144: claims the run first — if `worker_loop`'s poll already has it
    (or got there first), this is a silent, correct no-op instead of a
    second concurrent execution of the same stage's work. A heartbeat
    keeps the claim fresh while real progress is happening (see
    `run_check_run`'s own `heartbeat` parameter), so only a run that's
    made NO progress for `pipeline_claim_stale_seconds` is ever at risk of
    being reclaimed while this call is still genuinely working on it."""
    settings = settings or get_settings()
    session_factory = session_factory or db.get_session_factory()
    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        if check_run is None:
            return
        token = await _try_claim(session, check_run_id, settings)
        if token is None:
            return
        heartbeat, current_token = _make_heartbeat(session, check_run_id, token)
        try:
            # llm=None -- V-052 (BYOK): run_check_run resolves the caller's real
            # client itself, after it loads the manuscript, so it can select the
            # owning instructor's own key (falling back to the shared pool).
            await run_check_run(session, check_run, settings, heartbeat=heartbeat)
        finally:
            await _release_claim(session, check_run_id, current_token())


async def worker_tick(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
) -> bool:
    """Advances the single next-runnable check_run, if any. Returns
    whether one was found — the poll loop backs off when idle."""
    settings = settings or get_settings()
    session_factory = session_factory or db.get_session_factory()
    async with session_factory() as session:
        claimed = await pick_next_runnable(session, settings)
        if claimed is None:
            return False
        run, token = claimed
        heartbeat, current_token = _make_heartbeat(session, run.id, token)
        try:
            await run_check_run(session, run, settings, heartbeat=heartbeat)
        finally:
            await _release_claim(session, run.id, current_token())
        return True


async def worker_loop(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Runs forever (started as a background asyncio task at app startup);
    cancel it on shutdown. Polls immediately again after advancing a run
    (there may be more queued work), backs off to the configured interval
    when idle.

    BUG-032 finding: `run_check_run` already turns stage-level exceptions
    into an honest `failed` check_run, but a tick can still raise BEFORE
    reaching that try block (e.g. `pick_next_runnable`'s own query hitting
    a transient DB error) — and this loop has no supervisor, so an
    uncaught exception here would kill polling for every check_run, for
    the rest of the process's uptime, with nothing to restart it. One bad
    tick must degrade to "log and back off", never "the poller is gone."
    """
    settings = settings or get_settings()
    session_factory = session_factory or db.get_session_factory()
    while True:
        try:
            advanced = await worker_tick(session_factory, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker_tick failed; backing off and retrying")
            advanced = False
        await sleep(0.0 if advanced else settings.pipeline_worker_poll_seconds)
