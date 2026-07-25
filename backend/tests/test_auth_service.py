"""V-014: auth service — authenticate, session lifecycle. Needs a live
Postgres (same convention as the other integration suites); runs against
its own scratch database.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

import app.auth.service as auth_service
from app.auth.security import hash_password
from app.auth.service import (
    RateLimitedError,
    authenticate,
    create_session,
    delete_session,
    get_instructor_by_token,
)
from app.config import get_settings
from app.models.instructor import Instructor
from app.models.session import Session

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_authtest"


@pytest.fixture(scope="module")
def auth_scratch_url():
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
def session_factory(auth_scratch_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url

    engine = create_async_engine(sqlalchemy_url(auth_scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The limiter is a process-wide singleton (deliberately, so it
    persists across requests) — tests must not see each other's attempts."""
    auth_service._rate_limiter = None
    yield
    auth_service._rate_limiter = None


@pytest.fixture(autouse=True)
async def _clean_auth_tables(session_factory):
    """The scratch DB is module-scoped; each test starts from an empty
    instructor/session slate (instructor.email is UNIQUE)."""
    async with session_factory() as session:
        await session.execute(text("TRUNCATE session, instructor RESTART IDENTITY CASCADE"))
        await session.commit()
    yield


@pytest.fixture()
async def instructor(session_factory):
    async with session_factory() as session:
        row = Instructor(
            email="prof@tip.edu.ph", display_name="Prof", password_hash=hash_password("s3cret!")
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def test_authenticate_succeeds_with_correct_credentials(session_factory, instructor):
    settings = get_settings()
    async with session_factory() as session:
        result = await authenticate(session, "prof@tip.edu.ph", "s3cret!", settings)
    assert result is not None
    assert result.id == instructor.id


async def test_authenticate_fails_with_wrong_password(session_factory, instructor):
    settings = get_settings()
    async with session_factory() as session:
        result = await authenticate(session, "prof@tip.edu.ph", "wrong", settings)
    assert result is None


async def test_authenticate_fails_for_unknown_email_same_as_wrong_password(session_factory):
    """No user enumeration: an unknown email returns None, not a distinct error."""
    settings = get_settings()
    async with session_factory() as session:
        result = await authenticate(session, "nobody@tip.edu.ph", "whatever", settings)
    assert result is None


async def test_repeated_failures_raise_rate_limited(session_factory, instructor, monkeypatch):
    monkeypatch.setenv("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "2")
    get_settings.cache_clear()
    settings = get_settings()
    async with session_factory() as session:
        assert await authenticate(session, "prof@tip.edu.ph", "wrong", settings) is None
        assert await authenticate(session, "prof@tip.edu.ph", "wrong", settings) is None
        with pytest.raises(RateLimitedError):
            await authenticate(
                session, "prof@tip.edu.ph", "s3cret!", settings
            )  # even the RIGHT password


async def test_create_and_lookup_session_round_trips(session_factory, instructor):
    settings = get_settings()
    async with session_factory() as session:
        row = await create_session(session, instructor, settings)
    async with session_factory() as session:
        found = await get_instructor_by_token(session, row.token)
    assert found is not None
    assert found.id == instructor.id


async def test_expired_session_returns_none_and_is_deleted(session_factory, instructor):
    async with session_factory() as session:
        row = Session(
            token="expired-token",
            instructor_id=instructor.id,
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        session.add(row)
        await session.commit()

    async with session_factory() as session:
        found = await get_instructor_by_token(session, "expired-token")
    assert found is None

    async with session_factory() as session:
        remaining = await session.scalar(select(Session).where(Session.token == "expired-token"))
    assert remaining is None  # server-side cleanup, not just a client-side ignore


async def test_logout_deletes_the_session_server_side(session_factory, instructor):
    settings = get_settings()
    async with session_factory() as session:
        row = await create_session(session, instructor, settings)
        token = row.token

    async with session_factory() as session:
        await delete_session(session, token)

    async with session_factory() as session:
        found = await get_instructor_by_token(session, token)
    assert found is None
