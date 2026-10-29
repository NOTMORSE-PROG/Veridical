"""A transaction holding a manuscript's source lock outlives the server's idle
transaction limit, which a slow render or download can exceed."""

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.models.manuscript import Manuscript
from app.models.run import CheckRun
from app.storage import access
from tests.test_assessment_revision_live import seed
from tests.test_optional_grade_comments_live import factory as factory
from tests.test_optional_grade_comments_live import scratch_url as scratch_url

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="needs PostgreSQL")

SERVER_LIMIT = "1s"
IDLE_SECONDS = 2.5


async def _manuscript(session, run_id):
    run = await session.get(CheckRun, run_id)
    return await session.get(Manuscript, run.manuscript_id)


@pytest.mark.asyncio
async def test_the_server_ends_a_transaction_left_idle_past_its_limit(factory):
    _owner, run, _ids, _ = await seed(factory)
    async with factory() as session:
        await session.execute(text(f"SET idle_in_transaction_session_timeout = '{SERVER_LIMIT}'"))
        await _manuscript(session, run)
        await asyncio.sleep(IDLE_SECONDS)
        with pytest.raises(DBAPIError):
            await session.execute(text("SELECT 1"))


async def _idle_limit_ms(session) -> int:
    return int(
        await session.scalar(
            text(
                "SELECT setting FROM pg_settings WHERE name = 'idle_in_transaction_session_timeout'"
            )
        )
    )


@pytest.mark.asyncio
async def test_a_source_lock_keeps_its_transaction_past_the_server_limit(factory, monkeypatch):
    configured = get_settings().model_copy(
        update={"source_read_idle_transaction_timeout_seconds": 120}
    )
    monkeypatch.setattr(access, "get_settings", lambda: configured)
    _owner, run, _ids, _ = await seed(factory)
    async with factory() as session:
        await session.execute(text(f"SET idle_in_transaction_session_timeout = '{SERVER_LIMIT}'"))
        await access.lock_source(session, await _manuscript(session, run))
        assert await _idle_limit_ms(session) == 120_000
        await asyncio.sleep(IDLE_SECONDS)
        assert await session.scalar(text("SELECT 1")) == 1
        await session.commit()
        # The longer limit belonged to that transaction only.
        assert await _idle_limit_ms(session) == 1_000
