"""Authentication service: login, session lifecycle, rate limiting (F9.1).

Login failures are always generic ("incorrect email or password") —
never revealing whether the email exists (charter rule 3's honesty
principle cuts both ways: don't leak who else uses the system either).
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
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
_password_change_limiter: LoginRateLimiter | None = None


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


def get_password_change_limiter(settings: Settings) -> LoginRateLimiter:
    """backend-critic finding (V-042, P2): an authenticated session is a
    lower bar than a real login (a stolen cookie, a shared/unlocked
    device), so the current-password check on this endpoint is exactly
    the kind of "credential recovery" oracle OWASP's Broken Authentication
    guidance says needs the same throttling as login itself -- otherwise
    it's an unthrottled way to brute-force the real password (valuable
    beyond this one session if reused elsewhere) or permanently lock the
    legitimate owner out. Same limits, same mechanism, a separate
    instance and key space (instructor id, not email) since these are
    different events."""
    global _password_change_limiter
    if _password_change_limiter is None:
        _password_change_limiter = LoginRateLimiter(
            max_attempts=settings.login_rate_limit_max_attempts,
            window_seconds=settings.login_rate_limit_window_seconds,
        )
    return _password_change_limiter


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


async def change_password(
    session: AsyncSession,
    instructor: Instructor,
    current_password: str,
    new_password: str,
    current_token: str,
    settings: Settings,
) -> None:
    """Settings screen (4u, V-042). Requires the CURRENT password (not just
    an active session) -- the same reasoning most account-settings UIs use:
    a left-open, unattended session shouldn't be enough on its own to lock
    the real owner out via a changed password.

    backend-critic finding (P1): a password change is a security-boundary
    event (OWASP Session Management Cheat Sheet: "invalidate every
    existing session tied to that account") -- without this, a
    compromised session cookie survives the exact recovery step meant to
    kill it. Every OTHER session for this instructor is revoked here;
    `current_token` (the request that just re-proved the password) is
    kept alive so the instructor isn't logged out by their own change.

    backend-critic finding (P2): the current-password check is throttled
    the same as login (see `get_password_change_limiter`'s own docstring)
    -- an authenticated session is a lower bar than a real login, so this
    is a real brute-force oracle without it."""
    limiter = get_password_change_limiter(settings)
    key = str(instructor.id)
    if limiter.is_blocked(key):
        raise RateLimitedError(key)
    if instructor.password_hash is None or not verify_password(
        current_password, instructor.password_hash
    ):
        limiter.record_failure(key)
        raise WrongPasswordError
    limiter.reset(key)
    instructor.password_hash = hash_password(new_password)
    await session.execute(
        delete(Session).where(
            Session.instructor_id == instructor.id, Session.token != current_token
        )
    )
    await session.commit()


class WrongPasswordError(Exception):
    """Current password didn't verify -- distinct from the login flow's
    generic message: here the caller IS authenticated, so confirming
    "that password is wrong" leaks nothing new."""


async def mark_onboarding_dismissed(session: AsyncSession, instructor: Instructor) -> Instructor:
    """Flow A first-run welcome banner (V-055) / coach-mark tour (V-057):
    idempotent, re-dismissing just re-stamps the timestamp."""
    instructor.onboarding_dismissed_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(instructor)
    return instructor


async def mark_onboarding_replayed(session: AsyncSession, instructor: Instructor) -> Instructor:
    """V-057 AC4 (replayable): clears the dismissed timestamp so the
    coach-mark tour auto-starts again from its first step. Same field
    `mark_onboarding_dismissed` sets — one piece of state, not a second
    one — since the tour's current-step position lives client-side only
    (a fresh tour always restarts at step 1, matching both of the
    owner's reference screenshots' own "Replay this tour" affordance)."""
    instructor.onboarding_dismissed_at = None
    await session.commit()
    await session.refresh(instructor)
    return instructor
