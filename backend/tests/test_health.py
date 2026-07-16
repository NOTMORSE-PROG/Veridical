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
