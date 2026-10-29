import os

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


def test_health_reports_ok_when_db_reachable(monkeypatch):
    async def fake_check(dsn, timeout=5.0):
        return True, "ok"

    monkeypatch.setattr(db, "check_connectivity", fake_check)
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_health_reports_the_deployed_commit(monkeypatch):
    """BUG-418: production must say which build it is serving, so a backend
    that silently stopped deploying fails the smoke test instead of hiding."""
    from app.config import get_settings

    async def fake_check(dsn, timeout=5.0):
        return True, "ok"

    monkeypatch.setattr(db, "check_connectivity", fake_check)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123def456")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            body = client.get("/health").json()
    finally:
        get_settings.cache_clear()
    assert body["commit"] == "abc123def456"


def test_health_stays_200_and_honest_when_db_down(monkeypatch):
    """Failure taxonomy (TESTING.md §5): a down dependency is reported as

    'degraded', never conflated with an app error or hidden behind a 500."""

    async def fake_check(dsn, timeout=5.0):
        return False, "ConnectionRefusedError"

    monkeypatch.setattr(db, "check_connectivity", fake_check)
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["db"] == "ConnectionRefusedError"


@pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
async def test_db_connectivity_against_real_database():
    ok, detail = await db.check_connectivity(os.environ["DATABASE_URL"])
    assert ok, f"DATABASE_URL set but unreachable: {detail}"
    assert detail == "ok"


def test_health_reuses_a_recent_database_check_when_configured(monkeypatch):
    """A platform probing /health every few seconds should not pay a new
    database connection each time on a small host."""
    from app import main
    from app.config import get_settings

    calls = []

    async def fake_check(dsn, timeout=5.0):
        calls.append(dsn)
        return True, "ok"

    monkeypatch.setattr(db, "check_connectivity", fake_check)
    monkeypatch.setattr(main, "_last_db_check", None)
    monkeypatch.setenv("HEALTH_DB_CHECK_CACHE_SECONDS", "30")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            assert client.get("/health").json()["db"] == "ok"
            assert client.get("/health").json()["db"] == "ok"
    finally:
        get_settings.cache_clear()
    assert len(calls) == 1
