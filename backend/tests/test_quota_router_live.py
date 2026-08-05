"""V-009: GET /quota — dashboard meter (screens 4e/4u). Auth-gated
(BUG-007/D-020) — same live-DB convention as test_pipeline_router_live.py.
"""

import os

import pytest
from fastapi.testclient import TestClient

import app.auth.service as auth_service
from app.auth.security import hash_password
from app.config import get_settings

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_quotaapitest"


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
def client(api_scratch_url, monkeypatch):
    import app.db as db
    import app.ratelimit as ratelimit

    monkeypatch.setenv("DATABASE_URL", api_scratch_url)
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    get_settings.cache_clear()
    db._engine = None
    auth_service._rate_limiter = None
    ratelimit._limiters.clear()
    from app.main import app

    with TestClient(app) as c:
        yield c
    db._engine = None


@pytest.fixture()
def logged_in(client, api_scratch_url):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.instructor import Instructor

    async def seed():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                session.add(
                    Instructor(
                        email="prof@tip.edu.ph",
                        display_name="Prof",
                        password_hash=hash_password("s3cret!"),
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    client.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    return client


def test_quota_route_requires_auth(client):
    resp = client.get("/quota")
    assert resp.status_code == 401


def test_quota_route_reports_fake_mode_honestly(logged_in):
    resp = logged_in.get("/quota")

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "fake"
    assert body["calls_used"] == 0
    assert body["cache_hit_rate"] == 0.0
    assert "reset_at" in body
