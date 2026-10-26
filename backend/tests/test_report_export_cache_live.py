"""Private export ownership/assembly stays real; only the final renderer is stubbed."""

import asyncio
import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.ratelimit as ratelimit
import app.report.router as report_router
from app.config import get_settings
from app.db import sqlalchemy_url
from app.models.enums import CheckRunStatus
from app.models.run import CheckRun
from tests.test_ingest_fixtures import FIXTURE_DIR
from tests.test_share_router_live import (  # noqa: F401 -- shared integration fixtures
    api_scratch_url,
    client,
    logged_in_with_a_done_run,
)

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="integration: requires PostgreSQL"
)


@pytest.fixture
def ready_export(logged_in_with_a_done_run, monkeypatch):  # noqa: F811 -- shared fixture
    tc, rid = logged_in_with_a_done_run
    original = (FIXTURE_DIR / "native.pdf").read_bytes()
    state = {"assembly": 0, "builder": 0}
    actual_assembly = report_router.get_report_export_data
    ratelimit._limiters.clear()

    async def assemble(*args, **kwargs):
        state["assembly"] += 1
        return await actual_assembly(*args, **kwargs)

    def existing_bytes(data):
        assert data.report.check_run_id == rid
        assert data.flags == []
        assert data.page_images == {}
        state["builder"] += 1
        return original

    monkeypatch.setattr(report_router, "get_report_export_data", assemble)
    monkeypatch.setattr(report_router, "build_report_pdf", existing_bytes)
    try:
        yield tc, rid, state, original
    finally:
        try:
            assert tc.post("/auth/logout").status_code == 200
        finally:
            ratelimit._limiters.clear()


def protected(response, status):
    assert response.status_code == status, response.text
    assert response.headers.get("cache-control") == "private, no-store"
    assert "x-robots-tag" not in response.headers
    return response


def test_real_export_ownership_preserves_bytes_and_stops_foreign_builder(ready_export):
    tc, rid, state, original = ready_export
    path = f"/check-runs/{rid}/report/export.pdf"
    response = protected(tc.get(path), 200)
    assert response.content == original
    assert response.headers["content-type"] == "application/pdf"
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="veridical-report-{rid}.pdf"'
    )
    assert state == {"assembly": 1, "builder": 1}
    assert tc.post("/auth/logout").status_code == 200
    unsigned = protected(tc.get(path), 401)
    assert unsigned.json() == {"error": {"code": "unauthenticated", "message": "Not signed in."}}
    assert state == {"assembly": 1, "builder": 1}
    assert (
        tc.post(
            "/auth/login", json={"email": "stranger@tip.edu.ph", "password": "other!"}
        ).status_code
        == 200
    )
    for requested in (rid, rid + 1000000):
        response = protected(tc.get(f"/check-runs/{requested}/report/export.pdf"), 404)
        assert response.json() == {
            "error": {"code": "not_found", "message": f"No check run with id {requested}."}
        }
    protected(tc.get("/check-runs/not-an-integer/report/export.pdf"), 422)
    assert state == {"assembly": 3, "builder": 1}


def test_incomplete_report_retains_conflict_without_building(ready_export, api_scratch_url):  # noqa: F811
    tc, rid, state, _ = ready_export

    async def incomplete():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            async with async_sessionmaker(engine)() as session:
                run = await session.get(CheckRun, rid)
                run.status = CheckRunStatus.semantic
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(incomplete())
    response = protected(tc.get(f"/check-runs/{rid}/report/export.pdf"), 409)
    assert response.json() == {
        "error": {
            "code": "conflict",
            "message": "This check hasn't finished yet. Its report isn't ready.",
        }
    }
    assert state == {"assembly": 1, "builder": 0}


def test_actual_export_limiter_stops_second_request_before_assembly(ready_export, monkeypatch):
    tc, rid, state, original = ready_export
    # Controlled one-request profile exercises the real configured limiter.
    monkeypatch.setenv("ACTION_RATE_LIMIT_MAX_ATTEMPTS", "1")
    get_settings.cache_clear()
    ratelimit._limiters.clear()
    assert get_settings().action_rate_limit_max_attempts == 1
    path = f"/check-runs/{rid}/report/export.pdf"
    assert protected(tc.get(path), 200).content == original
    assert state == {"assembly": 1, "builder": 1}
    response = protected(tc.get(path), 429)
    assert response.json() == {
        "error": {
            "code": "rate_limited",
            "message": "Too many requests, please wait a bit before trying again.",
        }
    }
    assert state == {"assembly": 1, "builder": 1}
