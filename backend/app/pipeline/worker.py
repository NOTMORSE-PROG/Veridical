"""F3 fair admission shared by request wakeups and durable polling."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import db
from app.config import Settings, get_settings
from app.loop_probe import LoopLagProbe
from app.memory import return_freed_memory
from app.models.run import CheckRun
from app.pipeline.coordination import Admission, AdmissionState, admit_next, release_claim
from app.pipeline.lease import ClaimLease
from app.pipeline.machine import run_check_run

logger = logging.getLogger(__name__)


async def _release_claim(session: AsyncSession, check_run_id: int, *tokens: datetime) -> None:
    await release_claim(session, check_run_id, *tokens)


async def pick_next_runnable(
    session: AsyncSession, settings: Settings | None = None
) -> tuple[CheckRun, datetime] | None:
    """Compatibility selector: a fresh claim anywhere reserves the global slot."""
    admission = await admit_next(session, settings or get_settings())
    if admission.state != AdmissionState.admitted:
        return None
    return admission.run, admission.token


async def _execute(
    admission: Admission, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> AdmissionState:
    lease = ClaimLease(factory, admission, settings)
    lease.start()
    probe = LoopLagProbe(settings.event_loop_probe_seconds).start()
    outcome = AdmissionState.admitted
    try:
        async with factory() as session:
            run = await session.get(CheckRun, lease.run_id)
            if run is not None:
                result = await run_check_run(
                    session,
                    run,
                    settings,
                    heartbeat=lease.checkpoint,
                    claim_token=lease.current_token,
                )
                if result is False:
                    outcome = AdmissionState.busy
    finally:
        # Closing the analytical session rolls back unsafe work before cleanup.
        await lease.stop()
        logger.info(
            "Check run %s turn ended; worst event-loop delay %.2f s",
            lease.run_id,
            await probe.stop(),
        )
        try:
            async with asyncio.timeout(settings.pipeline_coordination_timeout_seconds):
                async with factory() as cleanup:
                    await _release_claim(cleanup, lease.run_id, *lease.tokens())
        except Exception:
            logger.warning("Claim release deferred for check run %s", lease.run_id)
        # The run's extraction and stage results are freed; return the pages
        # (off the event loop, no full collection: see process_next_upload).
        await asyncio.to_thread(return_freed_memory, collect=False)
    return outcome


async def worker_step(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
    *,
    requested_run_id: int | None = None,
) -> AdmissionState:
    settings = settings or get_settings()
    factory = session_factory or db.get_session_factory()
    admission = None
    entered_execution = False
    admitted_id = admitted_token = None
    try:
        async with factory() as session:
            admission = await admit_next(session, settings, requested_run_id=requested_run_id)
            if admission.state == AdmissionState.admitted:
                admitted_id, admitted_token = admission.run.id, admission.token
        if admission.state != AdmissionState.admitted:
            return admission.state
        entered_execution = True
        return await _execute(admission, factory, settings)
    finally:
        # Admission already committed even if shutdown cancels session close.
        if admitted_id is not None and not entered_execution:
            async with asyncio.timeout(settings.pipeline_coordination_timeout_seconds):
                async with factory() as cleanup:
                    await _release_claim(cleanup, admitted_id, admitted_token)


async def worker_tick(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
) -> bool:
    return await worker_step(session_factory, settings) == AdmissionState.admitted


async def advance_once(
    check_run_id: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
) -> None:
    """Advance only this requested run when it wins common fair admission."""
    await worker_step(session_factory, settings, requested_run_id=check_run_id)


async def worker_loop(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    *,
    wake_event: asyncio.Event | None = None,
    periodic: bool = True,
) -> None:
    """Coalesce request wakeups; retain busy work and drain before waiting again.

    Production polling recovers durable work after restart. Without periodic
    polling, development waits for an explicit accepted-request wakeup.
    """
    settings = settings or get_settings()
    pending = periodic
    while True:
        if wake_event is not None and not pending:
            if periodic:
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        wake_event.wait(), timeout=settings.pipeline_worker_poll_seconds
                    )
            else:
                await wake_event.wait()
        if wake_event is not None:
            # Clear BEFORE inspecting the DB, never after the final idle check.
            wake_event.clear()
        try:
            state = await worker_step(session_factory, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker_step failed; backing off and retrying")
            state = AdmissionState.busy
        pending = state != AdmissionState.idle
        if wake_event is None or pending:
            await sleep(
                0.0 if state == AdmissionState.admitted else settings.pipeline_worker_poll_seconds
            )
