"""Background job runner (ticket research note: simplest option, no paid
queue infrastructure) — one check_run advances at a time, matching the
free dyno's single-worker reality (ENGINEERING §4). A poll loop is
started at app startup (`app.main`'s lifespan) for steady-state progress;
`advance_once` lets the create-check-run route kick a freshly queued run
immediately instead of waiting for the next poll tick.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import db
from app.config import Settings, get_settings
from app.llm import get_llm_client
from app.models.enums import CheckRunStatus
from app.models.run import CheckRun
from app.pipeline.machine import is_blocked, run_check_run

_NON_TERMINAL = (
    CheckRunStatus.queued,
    CheckRunStatus.ingesting,
    CheckRunStatus.structural,
    CheckRunStatus.semantic,
    CheckRunStatus.integrity,
    CheckRunStatus.aggregating,
)


async def pick_next_runnable(session: AsyncSession) -> CheckRun | None:
    """Oldest non-terminal run that isn't currently PARKED (quota/api_down
    resume_at in the future) — one at a time, FIFO, per the free-dyno
    constraint (ticket AC: a second upload while running just queues)."""
    candidates = await session.scalars(
        select(CheckRun).where(CheckRun.status.in_(_NON_TERMINAL)).order_by(CheckRun.created_at)
    )
    now = datetime.now(UTC)
    for run in candidates:
        if not is_blocked(run, now=now):
            return run
    return None


async def advance_once(
    check_run_id: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
) -> None:
    """Advances ONE specific check_run to completion or its next block —
    used both by the create-route's "kick it now" call and by tests."""
    settings = settings or get_settings()
    session_factory = session_factory or db.get_session_factory()
    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        if check_run is None:
            return
        await run_check_run(session, check_run, settings, get_llm_client(settings))


async def worker_tick(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
) -> bool:
    """Advances the single next-runnable check_run, if any. Returns
    whether one was found — the poll loop backs off when idle."""
    settings = settings or get_settings()
    session_factory = session_factory or db.get_session_factory()
    async with session_factory() as session:
        run = await pick_next_runnable(session)
        if run is None:
            return False
        await run_check_run(session, run, settings, get_llm_client(settings))
        return True


async def worker_loop(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Runs forever (started as a background asyncio task at app startup);
    cancel it on shutdown. Polls immediately again after advancing a run
    (there may be more queued work), backs off to the configured interval
    when idle."""
    settings = settings or get_settings()
    session_factory = session_factory or db.get_session_factory()
    while True:
        advanced = await worker_tick(session_factory, settings)
        await sleep(0.0 if advanced else settings.pipeline_worker_poll_seconds)
