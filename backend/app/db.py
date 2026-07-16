"""Database access. V-001 scope: connectivity check only (schema is V-003)."""

import asyncio

import asyncpg


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
