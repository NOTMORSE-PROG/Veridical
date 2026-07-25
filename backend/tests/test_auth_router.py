"""V-014: POST /auth/login, /auth/logout, GET /auth/me — the HTTP surface.
Needs a live Postgres (same convention as test_ingest_api.py).
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

SCRATCH_DB = "veridical_authapitest"


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
def seeded(client, api_scratch_url):
    """Seeds via its OWN engine, never `app.db.get_engine()` — that engine
    is lazily bound to whichever event loop first uses it, and TestClient
    runs its own. Reaching into the shared one from a separate
    `asyncio.run()` call corrupted it across loops on Windows (asyncpg
    connections are loop-bound); a disposable engine here avoids that."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.instructor import Instructor

    async def seed():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine)
            async with factory() as session:
                # Module-scoped scratch DB: start from an empty slate
                # (instructor.email is UNIQUE).
                await session.execute(text("TRUNCATE session, instructor RESTART IDENTITY CASCADE"))
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
    return client


def test_login_wrong_password_is_generic_and_never_leaks_the_hash(seeded):
    resp = seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "wrong"})
    assert resp.status_code == 401
    body = resp.text
    assert "s3cret" not in body
    assert "$argon2" not in body  # the hash format prefix must never reach the client


def test_login_unknown_email_gets_the_same_generic_error(seeded):
    resp = seeded.post("/auth/login", json={"email": "nobody@tip.edu.ph", "password": "whatever"})
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == "Incorrect email or password."


def test_login_success_sets_httponly_cookie_and_me_then_works(seeded):
    resp = seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "prof@tip.edu.ph"
    assert seeded.cookies.get(get_settings().session_cookie_name) is not None

    me = seeded.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "prof@tip.edu.ph"


def test_me_without_a_session_is_401(seeded):
    resp = seeded.get("/auth/me")
    assert resp.status_code == 401


def test_repeated_wrong_passwords_get_rate_limited(seeded, monkeypatch):
    monkeypatch.setenv("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "2")
    get_settings.cache_clear()
    auth_service._rate_limiter = None
    for _ in range(2):
        resp = seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "wrong"})
        assert resp.status_code == 401
    blocked = seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    assert blocked.status_code == 401
    assert "many failed" in blocked.json()["error"]["message"].lower()


def test_logout_kills_the_session_server_side(seeded):
    seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    assert seeded.get("/auth/me").status_code == 200

    logout = seeded.post("/auth/logout")
    assert logout.status_code == 200

    after = seeded.get("/auth/me")
    assert after.status_code == 401
