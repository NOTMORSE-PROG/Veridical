"""V-018 HTTP surface smoke test: POST/GET /check-runs, auth-gated (same
convention as test_auth_router.py), fake-LLM mode end-to-end through the
real FastAPI app.
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

SCRATCH_DB = "veridical_pipelineapitest"


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
def client(api_scratch_url, tmp_path, monkeypatch):
    import app.db as db

    monkeypatch.setenv("DATABASE_URL", api_scratch_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    get_settings.cache_clear()
    db._engine = None
    auth_service._rate_limiter = None
    import app.ratelimit as ratelimit

    ratelimit._limiters.clear()  # BUG-004: don't leak check-run counts across tests
    from app.main import app

    with TestClient(app) as c:
        yield c
    db._engine = None
    auth_service._rate_limiter = None


@pytest.fixture()
def logged_in(client, api_scratch_url, tmp_path):
    """Seeds an instructor + an ingested manuscript + an active rubric,
    logs in, and returns (client, manuscript_id, rubric_id)."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.enums import IngestStatus
    from app.models.instructor import Instructor
    from app.models.manuscript import Manuscript
    from app.models.rubric import Rubric

    async def seed():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    text(
                        "TRUNCATE check_run, criterion, rubric, manuscript, "
                        "instructor RESTART IDENTITY CASCADE"
                    )
                )
                instructor = Instructor(
                    email="prof@tip.edu.ph",
                    display_name="Prof",
                    password_hash=hash_password("s3cret!"),
                )
                session.add(instructor)
                await session.commit()
                manuscript = Manuscript(
                    instructor_id=instructor.id,
                    group_label="G-11",
                    file_ref="x.pdf",
                    ingest_status=IngestStatus.done,
                )
                rubric = Rubric(
                    instructor_id=instructor.id,
                    title="Format",
                    source_file="r.pdf",
                    is_active=True,
                )
                session.add_all([manuscript, rubric])
                await session.commit()
                return manuscript.id, rubric.id
        finally:
            await engine.dispose()

    manuscript_id, rubric_id = asyncio.run(seed())
    client.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    return client, manuscript_id, rubric_id


def test_create_check_run_requires_auth(client):
    resp = client.post("/check-runs", json={"manuscript_id": 1, "rubric_id": 1})
    assert resp.status_code == 401


def test_create_get_and_list_check_run(logged_in):
    client, manuscript_id, rubric_id = logged_in
    created = client.post(
        "/check-runs", json={"manuscript_id": manuscript_id, "rubric_id": rubric_id}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "queued"
    assert body["queue_position"] == 1

    fetched = client.get(f"/check-runs/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["manuscript_id"] == manuscript_id

    listed = client.get("/check-runs")
    assert listed.status_code == 200
    assert any(r["id"] == body["id"] for r in listed.json())


def test_create_check_run_rejects_unconfirmed_rubric(logged_in):
    client, manuscript_id, _ = logged_in
    resp = client.post("/check-runs", json={"manuscript_id": manuscript_id, "rubric_id": 999999})
    assert resp.status_code == 404
