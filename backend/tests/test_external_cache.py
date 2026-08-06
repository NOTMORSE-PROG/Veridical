"""V-028: citation_cache reads/writes — live Postgres (own scratch DB, same
convention as test_llm_queue.py)."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import sqlalchemy_url
from app.external.cache import get_cached, store_result
from app.external.schemas import VerificationResult

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_externaltest"


@pytest.fixture(scope="module")
def scratch_url():
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
def session_factory(scratch_url):
    engine = create_async_engine(sqlalchemy_url(scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean(session_factory):
    async with session_factory() as session:
        await session.execute(text("TRUNCATE citation_cache"))
        await session.commit()
    yield


async def test_miss_then_hit(session_factory):
    async with session_factory() as session:
        assert await get_cached(session, key_kind="doi", key_value="10.1/x", stale_days=30) is None
        result = VerificationResult(found=True, provider="crossref", title="A Title")
        await store_result(
            session, key_kind="doi", key_value="10.1/x", provider="crossref", result=result
        )
        cached = await get_cached(session, key_kind="doi", key_value="10.1/x", stale_days=30)
        assert cached == result


async def test_second_write_overwrites_same_key(session_factory):
    async with session_factory() as session:
        first = VerificationResult(found=True, provider="crossref", title="First")
        await store_result(
            session, key_kind="doi", key_value="10.1/y", provider="crossref", result=first
        )
        second = VerificationResult(found=True, provider="crossref", title="Corrected Title")
        await store_result(
            session, key_kind="doi", key_value="10.1/y", provider="crossref", result=second
        )
        cached = await get_cached(session, key_kind="doi", key_value="10.1/y", stale_days=30)
        assert cached.title == "Corrected Title"


async def test_stale_row_reported_as_miss(session_factory):
    """A row older than `stale_days` must be re-verified (ticket edge case:
    retractions land late) — proven with `stale_days=0` against a row
    written moments ago, since the whole point of the staleness gate is a
    boundary condition, not a wall-clock wait in a test."""
    async with session_factory() as session:
        result = VerificationResult(found=True, provider="crossref", title="Old")
        await store_result(
            session, key_kind="doi", key_value="10.1/z", provider="crossref", result=result
        )
        fresh = await get_cached(session, key_kind="doi", key_value="10.1/z", stale_days=30)
        assert fresh is not None
        stale = await get_cached(session, key_kind="doi", key_value="10.1/z", stale_days=0)
        assert stale is None


async def test_different_key_kinds_dont_collide(session_factory):
    async with session_factory() as session:
        doi_result = VerificationResult(found=True, provider="crossref", title="Paper")
        isbn_result = VerificationResult(found=True, provider="openlibrary", title="Book")
        await store_result(
            session, key_kind="doi", key_value="978-0", provider="crossref", result=doi_result
        )
        await store_result(
            session, key_kind="isbn", key_value="978-0", provider="openlibrary", result=isbn_result
        )
        assert (
            await get_cached(session, key_kind="doi", key_value="978-0", stale_days=30)
        ).title == "Paper"
        assert (
            await get_cached(session, key_kind="isbn", key_value="978-0", stale_days=30)
        ).title == "Book"
