"""Serialize source cache refill with retention changes without extra DB connections."""

import asyncio
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import GoneError
from app.models.manuscript import Manuscript


async def lock_source(session: AsyncSession, manuscript: Manuscript) -> None:
    # The request transaction owns this SHARE lock through response completion.
    # Deletion takes UPDATE under its existing advisory lock. Readers need no
    # second pool connection or reentrant advisory acquisition.
    await session.refresh(manuscript, with_for_update={"read": True})


async def read_source[T](
    session: AsyncSession,
    manuscript: Manuscript,
    reader: Callable[[], Awaitable[T]],
    *,
    allow_purged: bool = False,
    on_cancel: Callable[[T], None] | None = None,
) -> T:
    await lock_source(session, manuscript)
    if manuscript.purged_at is not None and not allow_purged:
        raise GoneError("This manuscript's source file was purged and can no longer be read.")
    work = asyncio.create_task(reader())
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        # Executor work cannot be forcibly cancelled. Drain it before request
        # teardown releases the row lock, including repeated client cancellation.
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not work.cancelled() and work.exception() is None and on_cancel is not None:
            on_cancel(work.result())
        raise
