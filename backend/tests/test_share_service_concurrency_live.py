"""V-040: backend-critic finding (P1, live-reproduced) -- 15 truly
concurrent `POST /check-runs/{id}/share` requests used to leave 13
simultaneously "active" rows for the same check_run (a check-then-act
race, no DB-level guarantee). Reproduces the exact scenario against the
real ASGI app + real Postgres with genuinely concurrent async requests
(`asyncio.gather` over the real dependency-injected request path, each
with its own DB session -- not sequential TestClient calls, which would
serialize through one connection and never actually race) to prove the
fix -- a partial unique index (migration 0020) + a bounded retry loop --
holds under real concurrency, not just the common one-request-at-a-time
case the router tests already cover.
"""

import os
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select, text

from app.auth.security import hash_password

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_shareconcurrencytest"


@pytest.fixture(scope="module")
def api_scratch_url():
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
async def app(api_scratch_url, tmp_path, monkeypatch):
    import app.auth.service as auth_service
    import app.db as db
    from app.config import get_settings

    monkeypatch.setenv("DATABASE_URL", api_scratch_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    get_settings.cache_clear()
    db._engine = None
    auth_service._rate_limiter = None
    from app.main import app as fastapi_app

    async with fastapi_app.router.lifespan_context(fastapi_app):
        yield fastapi_app
    db._engine = None
    auth_service._rate_limiter = None


@pytest.fixture()
async def seeded(app, api_scratch_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.enums import CheckRunStatus
    from app.models.instructor import Instructor
    from app.models.manuscript import Manuscript
    from app.models.rubric import Criterion, Rubric
    from app.models.run import CheckRun

    engine = create_async_engine(sqlalchemy_url(api_scratch_url))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await session.execute(
                text(
                    "TRUNCATE share_link, check_run, criterion, rubric, manuscript, instructor "
                    "RESTART IDENTITY CASCADE"
                )
            )
            instructor = Instructor(
                email="prof@tip.edu.ph",
                display_name="Prof",
                password_hash=hash_password("s3cret!"),
            )
            session.add(instructor)
            await session.commit()
            manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
            rubric = Rubric(
                instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=True
            )
            session.add_all([manuscript, rubric])
            await session.commit()
            session.add(
                Criterion(
                    rubric_id=rubric.id,
                    type="structural",
                    text="Has an abstract",
                    evidence=None,
                    weight=Decimal("100"),
                    position=0,
                )
            )
            check_run = CheckRun(
                manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
            )
            session.add(check_run)
            await session.commit()
            return check_run.id
    finally:
        await engine.dispose()


async def test_15_concurrent_create_requests_leave_exactly_one_active_link(
    app, seeded, api_scratch_url
):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.share import ShareLink

    check_run_id = seeded
    transport = httpx.ASGITransport(app=app)

    async def one_request() -> httpx.Response:
        # Each concurrent "request" gets its own httpx client (own cookie
        # jar), same as real concurrent browser tabs/requests would --
        # sharing one client across gather() would just serialize the
        # login+post pair and never actually race the create endpoint.
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"}
            )
            return await client.post(f"/check-runs/{check_run_id}/share", json={})

    responses = await asyncio.gather(*(one_request() for _ in range(15)))
    assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]

    engine = create_async_engine(sqlalchemy_url(api_scratch_url))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            active_count = await session.scalar(
                select(func.count())
                .select_from(ShareLink)
                .where(ShareLink.check_run_id == check_run_id, ShareLink.revoked_at.is_(None))
            )
            assert active_count == 1

            total_count = await session.scalar(
                select(func.count())
                .select_from(ShareLink)
                .where(ShareLink.check_run_id == check_run_id)
            )
            # Every request still created a real, individually-auditable
            # row -- concurrency safety doesn't mean silently dropping 14
            # of them.
            assert total_count == 15
    finally:
        await engine.dispose()
