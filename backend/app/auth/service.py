"""Authentication service: login, session lifecycle, rate limiting (F9.1).

Login failures are always generic ("incorrect email or password") —
never revealing whether the email exists (charter rule 3's honesty
principle cuts both ways: don't leak who else uses the system either).
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rate_limit import LoginRateLimiter
from app.auth.security import hash_password, verify_password
from app.config import Settings
from app.models.instructor import Instructor
from app.models.session import Session

# A real hash of a password nobody will ever type — verifying against it
# on a "no such email" miss spends roughly the same CPU as a real
# password check, so the response time doesn't reveal account existence.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))

_rate_limiter: LoginRateLimiter | None = None


def get_rate_limiter(settings: Settings) -> LoginRateLimiter:
    """Process-wide singleton: the attempt history must persist across
    requests within one process (same reasoning as the LLM RateGovernor,
    app/llm/__init__.py)."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = LoginRateLimiter(
            max_attempts=settings.login_rate_limit_max_attempts,
            window_seconds=settings.login_rate_limit_window_seconds,
        )
    return _rate_limiter


class RateLimitedError(Exception):
    """Too many recent failed attempts for this email."""


async def authenticate(
    session: AsyncSession, email: str, password: str, settings: Settings
) -> Instructor | None:
    limiter = get_rate_limiter(settings)
    key = email.strip().lower()
    if limiter.is_blocked(key):
        raise RateLimitedError(key)

    instructor = await session.scalar(select(Instructor).where(Instructor.email == key))
    if instructor is None or instructor.password_hash is None:
        verify_password(password, _DUMMY_HASH)  # constant-effort miss
        limiter.record_failure(key)
        return None
    if not verify_password(password, instructor.password_hash):
        limiter.record_failure(key)
        return None
    limiter.reset(key)
    return instructor


async def create_session(
    session: AsyncSession, instructor: Instructor, settings: Settings
) -> Session:
    row = Session(
        token=secrets.token_urlsafe(32),
        instructor_id=instructor.id,
        expires_at=datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours),
    )
    session.add(row)
    await session.commit()
    return row


async def get_instructor_by_token(session: AsyncSession, token: str) -> Instructor | None:
    row = await session.get(Session, token)
    if row is None:
        return None
    if row.expires_at < datetime.now(UTC):
        await session.delete(row)
        await session.commit()
        return None
    return await session.get(Instructor, row.instructor_id)


async def delete_session(session: AsyncSession, token: str) -> None:
    row = await session.get(Session, token)
    if row is not None:
        await session.delete(row)
        await session.commit()


async def mark_onboarding_dismissed(session: AsyncSession, instructor: Instructor) -> Instructor:
    """Flow A first-run welcome banner (V-055): idempotent, re-dismissing
    just re-stamps the timestamp."""
    instructor.onboarding_dismissed_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(instructor)
    return instructor
