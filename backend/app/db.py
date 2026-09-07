"""Database access: connectivity check (V-001), SQLAlchemy URL helper
(V-003), request-scoped session dependency (V-008)."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


def sqlalchemy_url(dsn: str) -> str:
    """Name the async driver in a plain-postgres DSN.

    DATABASE_URL stays a standard `postgresql://` DSN (asyncpg and psql
    read it directly); SQLAlchemy needs the driver spelled out, and
    asyncpg is the only one installed. `postgres://` is accepted too —
    some hosts (Neon among them) still issue the legacy scheme.

    Neon's pooled DSN adds `sslmode=require&channel_binding=require` (libpq
    query params). SQLAlchemy's asyncpg dialect forwards every query param
    verbatim as a kwarg to `asyncpg.connect()` (it does no libpq
    translation), and asyncpg accepts neither key directly — found live
    against the Neon DSN: `TypeError: connect() got an unexpected keyword
    argument 'sslmode'`. `channel_binding` has no asyncpg equivalent and is
    dropped; `sslmode` becomes asyncpg's own `ssl` parameter, which does
    accept the same value strings (require/verify-ca/verify-full/...).
    """
    for scheme in ("postgresql://", "postgres://"):
        if dsn.startswith(scheme):
            dsn = "postgresql+asyncpg://" + dsn.removeprefix(scheme)
            break
    else:
        return dsn

    parts = urlsplit(dsn)
    if not parts.query:
        return dsn
    query = dict(parse_qsl(parts.query))
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode is not None:
        query["ssl"] = sslmode
    return urlunsplit(parts._replace(query=urlencode(query)))


_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """Process-wide lazy engine — created on first use, never at import
    (Neon suspends when idle; connecting eagerly at boot would stall
    startup, ENGINEERING.md §7)."""
    global _engine
    if _engine is None:
        # hide_parameters: BUG-032 finding — SQLAlchemy's default exception
        # __str__ embeds the full failing statement AND its literal bound
        # values (manuscript text, instructor email, ...). Those exception
        # strings get stored verbatim as a failed check_run's stage_status
        # message and returned over the API — this must never carry real
        # data, the same "never the DSN or credentials" bar check_connectivity
        # already holds itself to above.
        _engine = create_async_engine(
            sqlalchemy_url(get_settings().database_url), hide_parameters=True
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory for callers that outlive a single request (the LLM
    queue, V-009) and must open/close their own short-lived sessions."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request (CODING.md §2)."""
    async with get_session_factory()() as session:
        yield session


@asynccontextmanager
async def advisory_lock(session: AsyncSession, key: int) -> AsyncIterator[None]:
    """Holds a session-scoped Postgres advisory lock on `key` for the
    duration of the `async with` block, on a DEDICATED connection
    independent of `session`'s own connection lifecycle (BUG-178,
    `backend-critic` finding, empirically proven against this app's own
    engine/pool defaults: `pg_advisory_lock`/`pg_advisory_unlock` called
    directly on a passed-in `AsyncSession` broke under real pool
    contention -- a session's physical connection is NOT stable across
    `session.commit()`, so the pool can hand back a DIFFERENT connection
    for the next statement; a lock acquired on the session's connection
    then gets "released" on a connection that never held it, while the
    REAL lock stays orphaned on whatever connection the session moved to.
    `pg_advisory_unlock`'s own return value caught this directly (`False`
    -- Postgres itself reporting the caller didn't hold what it thought it
    held); this function checks that return value and fails loud instead
    of discarding the signal.

    Bound to the SAME engine `session` itself uses (`session.get_bind()`),
    never the process-wide `get_engine()` singleton directly -- so a
    caller under test (a scratch-DB `session_factory` fixture) locks
    against its own database, not whatever `get_engine()` happened to
    cache first. `AsyncSession.get_bind()` returns the plain SYNC
    `sqlalchemy.engine.Engine` facade (it delegates to the wrapped sync
    Session internally), not an `AsyncEngine` -- wrapped back into one
    here so `.connect()` supports `async with`; confirmed live that this
    wrapping does not create a second, independent connection pool (it
    shares the same underlying sync `Engine`/pool `session`'s own engine
    already uses)."""
    engine = AsyncEngine(session.get_bind())
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        try:
            yield
        finally:
            released = await conn.scalar(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            if not released:
                raise RuntimeError(
                    f"pg_advisory_unlock({key}) reported no lock was held by this "
                    "connection -- this should be structurally impossible; treat "
                    "any concurrent corpus withdrawal/write-back near this key as "
                    "unverified until investigated."
                )


async def check_connectivity(dsn: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Run SELECT 1 against the database.

    Connects lazily and retries once: Neon free tier suspends compute when
    idle, so the first attempt may time out while it wakes.
    Returns (ok, detail) — detail is the exception class name on failure,
    never the DSN or credentials.
    """
    last_error = "unknown"
    for attempt in range(2):
        try:
            conn = await asyncpg.connect(dsn, timeout=timeout)
            try:
                await conn.fetchval("SELECT 1")
            finally:
                await conn.close()
            return True, "ok"
        except (OSError, asyncpg.PostgresError) as exc:  # TimeoutError ⊂ OSError
            last_error = type(exc).__name__
            if attempt == 0:
                await asyncio.sleep(0.5)
    return False, last_error
