"""Database access: connectivity check (V-001), SQLAlchemy URL helper
(V-003), request-scoped session dependency (V-008)."""

import asyncio
from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg
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
