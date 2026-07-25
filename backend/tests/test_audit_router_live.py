"""V-024 HTTP surface smoke test: GET /audit(/{{id}}), auth-gated, same
convention as test_report_router_live.py. Scoping/filtering logic itself
is covered at the service layer (test_audit_live.py) — this file only
proves the HTTP wiring (auth guard, status codes, response shape).
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

SCRATCH_DB = "veridical_auditapitest"


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
    from app.main import app

    with TestClient(app) as c:
        yield c
    db._engine = None
    auth_service._rate_limiter = None


@pytest.fixture()
def logged_in_with_one_audit_row(client, api_scratch_url):
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.audit import AuditLog
    from app.models.instructor import Instructor
    from app.models.manuscript import Manuscript
    from app.models.rubric import Rubric
    from app.models.run import CheckRun

    async def seed():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    text(
                        "TRUNCATE audit_log, check_run, rubric, manuscript, "
                        "instructor RESTART IDENTITY CASCADE"
                    )
                )
                instructor = Instructor(
                    email="audit@tip.edu.ph",
                    display_name="Audit Prof",
                    password_hash=hash_password("s3cret!"),
                )
                session.add(instructor)
                await session.commit()
                manuscript = Manuscript(
                    instructor_id=instructor.id, group_label="G-11", file_ref="x.pdf"
                )
                rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
                session.add_all([manuscript, rubric])
                await session.commit()
                check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
                session.add(check_run)
                await session.commit()
                session.add(
                    AuditLog(
                        event_type="llm_call",
                        check_run_id=check_run.id,
                        prompt_version="v1",
                        payload={"prompt_type": "semantic_grading", "response": {}},
                    )
                )
                await session.commit()
                return check_run.id
        finally:
            await engine.dispose()

    check_run_id = asyncio.run(seed())
    client.post("/auth/login", json={"email": "audit@tip.edu.ph", "password": "s3cret!"})
    return client, check_run_id


def test_list_audit_log_requires_auth(client):
    resp = client.get("/audit")
    assert resp.status_code == 401


def test_get_audit_detail_requires_auth(client):
    resp = client.get("/audit/1")
    assert resp.status_code == 401


def test_list_and_detail_return_the_seeded_row(logged_in_with_one_audit_row):
    client, check_run_id = logged_in_with_one_audit_row

    listing = client.get("/audit")
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["event_type"] == "llm_call"
    assert body["items"][0]["check_run_id"] == check_run_id

    filtered = client.get(f"/audit?check_run_id={check_run_id}")
    assert filtered.json()["total"] == 1

    detail = client.get(f"/audit/{body['items'][0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["payload"]["prompt_type"] == "semantic_grading"


def test_detail_404s_for_a_nonexistent_row(logged_in_with_one_audit_row):
    client, _ = logged_in_with_one_audit_row
    resp = client.get("/audit/999999")
    assert resp.status_code == 404
