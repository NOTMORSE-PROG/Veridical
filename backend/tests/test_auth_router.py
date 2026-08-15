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
    auth_service._password_change_limiter = None
    from app.main import app

    with TestClient(app) as c:
        yield c
    db._engine = None
    auth_service._rate_limiter = None
    auth_service._password_change_limiter = None


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
    auth_service._password_change_limiter = None
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


def test_new_account_has_no_onboarding_dismissal_yet(seeded):
    seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    assert seeded.get("/auth/me").json()["onboarding_dismissed_at"] is None


def test_dismissing_onboarding_persists_across_a_fresh_session(seeded):
    seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    dismissed = seeded.post("/auth/onboarding/dismiss")
    assert dismissed.status_code == 200
    assert dismissed.json()["onboarding_dismissed_at"] is not None

    # Log out and back in (a "different session, not just a re-render") --
    # the flag must survive, proving it is NOT session/localStorage-only.
    seeded.post("/auth/logout")
    seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    assert seeded.get("/auth/me").json()["onboarding_dismissed_at"] is not None


def test_dismissing_onboarding_without_a_session_is_401(seeded):
    resp = seeded.post("/auth/onboarding/dismiss")
    assert resp.status_code == 401


def test_replaying_onboarding_clears_dismissal_and_persists(seeded):
    seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    seeded.post("/auth/onboarding/dismiss")
    assert seeded.get("/auth/me").json()["onboarding_dismissed_at"] is not None

    replayed = seeded.post("/auth/onboarding/replay")
    assert replayed.status_code == 200
    assert replayed.json()["onboarding_dismissed_at"] is None

    # Persists across a fresh session too, same as dismiss does.
    seeded.post("/auth/logout")
    seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    assert seeded.get("/auth/me").json()["onboarding_dismissed_at"] is None


def test_replaying_onboarding_without_a_session_is_401(seeded):
    resp = seeded.post("/auth/onboarding/replay")
    assert resp.status_code == 401


def test_changing_password_requires_the_current_one(seeded):
    """V-042 (settings, screen 4u): a left-open session alone must not be
    enough to lock the real owner out via a changed password."""
    seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    resp = seeded.post(
        "/auth/password",
        json={"current_password": "wrong-one", "new_password": "brand-new-pw"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == "Current password is incorrect."

    # The old password still works -- nothing was mutated on a rejected attempt.
    still_works = seeded.post(
        "/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"}
    )
    assert still_works.status_code == 200


def test_changing_password_succeeds_and_the_new_one_logs_in(seeded):
    seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    resp = seeded.post(
        "/auth/password",
        json={"current_password": "s3cret!", "new_password": "brand-new-pw"},
    )
    assert resp.status_code == 200

    seeded.post("/auth/logout")
    old = seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    assert old.status_code == 401


def test_changing_password_revokes_every_other_session_but_keeps_this_one(seeded):
    """backend-critic finding (P1): a password change is a security-
    boundary event -- a session left open on another device (or a stolen
    cookie) must not survive the exact recovery step meant to kill it,
    but the request that just re-proved the password shouldn't log
    itself out."""
    cookie_name = get_settings().session_cookie_name

    seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    other_device_token = seeded.cookies.get(cookie_name)

    # A second, independent session for the same account (a different
    # device/browser) -- logging in again issues a NEW token without
    # invalidating the first.
    seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    this_device_token = seeded.cookies.get(cookie_name)
    assert this_device_token != other_device_token

    resp = seeded.post(
        "/auth/password",
        json={"current_password": "s3cret!", "new_password": "brand-new-pw"},
    )
    assert resp.status_code == 200

    seeded.cookies.set(cookie_name, other_device_token)
    assert seeded.get("/auth/me").status_code == 401

    seeded.cookies.set(cookie_name, this_device_token)
    assert seeded.get("/auth/me").status_code == 200


def test_changing_password_is_rate_limited_like_login(seeded, monkeypatch):
    monkeypatch.setenv("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "2")
    get_settings.cache_clear()
    auth_service._password_change_limiter = None
    seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})

    for _ in range(2):
        resp = seeded.post(
            "/auth/password",
            json={"current_password": "wrong-one", "new_password": "brand-new-pw"},
        )
        assert resp.status_code == 401
    blocked = seeded.post(
        "/auth/password",
        json={"current_password": "s3cret!", "new_password": "brand-new-pw"},
    )
    assert blocked.status_code == 401
    assert "many attempts" in blocked.json()["error"]["message"].lower()
    # Blocked even with the CORRECT current password -- the password was
    # never actually changed by any of the three attempts.
    still_old = seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    assert still_old.status_code == 200


def test_changing_password_rejects_a_too_short_new_password(seeded):
    seeded.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    resp = seeded.post(
        "/auth/password", json={"current_password": "s3cret!", "new_password": "short"}
    )
    assert resp.status_code == 422


def test_changing_password_without_a_session_is_401(seeded):
    resp = seeded.post(
        "/auth/password", json={"current_password": "s3cret!", "new_password": "brand-new-pw"}
    )
    assert resp.status_code == 401
