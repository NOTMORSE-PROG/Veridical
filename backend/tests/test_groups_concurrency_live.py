"""V-062: `backend-critic` finding (P2, live-reproduced) -- concurrent
uploads for the same brand-new group name used to race a select-then-
insert against `manuscript_group`'s unique constraint, and the loser got
an unhandled 500 (`IntegrityError` uncaught). Reproduces the exact
scenario against the real ASGI app + real Postgres with genuinely
concurrent async requests (`asyncio.gather`, each with its own session --
not sequential TestClient calls, which serialize through one connection
and never actually race) to prove the retry loop
(`app/groups/service.py::resolve_or_create_group`) holds under real
concurrency. Same shape as `test_share_service_concurrency_live.py`.
"""

import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select, text

from app.auth.security import hash_password

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_groupsconcurrencytest"
FIXTURE_PDF = Path(__file__).parent / "fixtures" / "ingest" / "native.pdf"


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
async def seeded_instructor(app, api_scratch_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.instructor import Instructor

    engine = create_async_engine(sqlalchemy_url(api_scratch_url))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await session.execute(
                text("TRUNCATE manuscript_group, manuscript, instructor RESTART IDENTITY CASCADE")
            )
            instructor = Instructor(
                email="prof@tip.edu.ph",
                display_name="Prof",
                password_hash=hash_password("s3cret!"),
            )
            session.add(instructor)
            await session.commit()
            return instructor.id
    finally:
        await engine.dispose()


async def test_concurrent_uploads_for_a_brand_new_group_name_never_500_and_share_one_group(
    app, seeded_instructor, api_scratch_url
):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.group import Group

    content = FIXTURE_PDF.read_bytes()
    transport = httpx.ASGITransport(app=app)

    async def one_upload(i: int) -> httpx.Response:
        # Own client (own cookie jar) per "request" -- sharing one client
        # across gather() would serialize login+post and never actually race.
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"}
            )
            return await client.post(
                "/manuscripts/ingest",
                files={"file": (f"m{i}.pdf", content, "application/octet-stream")},
                data={"group_label": "Race Condition Team"},
            )

    responses = await asyncio.gather(*(one_upload(i) for i in range(10)))
    assert all(r.status_code == 200 for r in responses), [
        (r.status_code, r.text) for r in responses
    ]
    # Every response resolved to the SAME group -- no client silently got
    # a different (duplicate) group just because it lost the race.
    assert {r.json()["group_label"] for r in responses} == {"Race Condition Team"}

    engine = create_async_engine(sqlalchemy_url(api_scratch_url))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            group_count = await session.scalar(
                select(func.count())
                .select_from(Group)
                .where(
                    Group.instructor_id == seeded_instructor,
                    Group.name == "Race Condition Team",
                )
            )
            assert group_count == 1
    finally:
        await engine.dispose()
