"""V-020 HTTP surface smoke test: GET /check-runs/{id}/report, auth-gated,
same convention as test_pipeline_router_live.py.
"""

import os
from decimal import Decimal

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

SCRATCH_DB = "veridical_reportapitest"


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
def logged_in_with_a_done_run(client, api_scratch_url):
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.enums import CheckKind, CheckRunStatus, ResultOutcome
    from app.models.instructor import Instructor
    from app.models.manuscript import Manuscript
    from app.models.rubric import Criterion, Rubric
    from app.models.run import CheckResult, CheckRun
    from app.report.service import aggregate_and_score

    async def seed():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    text(
                        "TRUNCATE readiness_report, check_result, check_run, criterion, "
                        "rubric, manuscript, instructor RESTART IDENTITY CASCADE"
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
                    instructor_id=instructor.id, group_label="G-11", file_ref="x.pdf"
                )
                rubric = Rubric(
                    instructor_id=instructor.id,
                    title="Format",
                    source_file="r.pdf",
                    is_active=True,  # the normal case: checks run against an active rubric
                )
                session.add_all([manuscript, rubric])
                await session.commit()
                criterion = Criterion(
                    rubric_id=rubric.id,
                    type="structural",
                    text="Has an abstract",
                    evidence=None,
                    weight=Decimal("100"),
                    position=0,
                )
                session.add(criterion)
                await session.commit()
                check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
                session.add(check_run)
                await session.commit()
                session.add(
                    CheckResult(
                        check_run_id=check_run.id,
                        criterion_id=criterion.id,
                        kind=CheckKind.structural,
                        outcome=ResultOutcome.passed,
                        score=100.0,
                        detail={"rule_id": "required_section_present", "anchor": "page 2"},
                    )
                )
                check_run.status = CheckRunStatus.done
                await session.commit()
                await aggregate_and_score(session, check_run.id)
                return check_run.id
        finally:
            await engine.dispose()

    check_run_id = asyncio.run(seed())
    client.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    return client, check_run_id


def test_get_report_requires_auth(client):
    resp = client.get("/check-runs/1/report")
    assert resp.status_code == 401


def test_get_report_returns_the_full_shape(logged_in_with_a_done_run):
    client, check_run_id = logged_in_with_a_done_run
    resp = client.get(f"/check-runs/{check_run_id}/report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["composite_score"] == 100.0
    assert body["thresholds"]["ready_min_score"] == 85.0
    assert len(body["results"]) == 1
    assert body["results"][0]["anchor"] == "page 2"
    # Already computed by score_check_run but previously dropped before
    # reaching the API — screen 4h needs these to state the actual
    # determining factor instead of hedging with "or" (V-055 review).
    assert body["flag_deduction"] == 0.0
    assert body["unresolved_high_flag_count"] == 0
    # V-038: unset by default, no gate pending, current rubric.
    assert body["decision"] is None
    assert body["pending_review_count"] == 0
    assert body["rubric_is_current"] is True


def test_decision_route_requires_auth(client):
    resp = client.post("/check-runs/1/decision", json={"decision": "approved"})
    assert resp.status_code == 401


def test_decide_then_reopen_over_http(logged_in_with_a_done_run):
    client, check_run_id = logged_in_with_a_done_run
    resp = client.post(
        f"/check-runs/{check_run_id}/decision",
        json={"decision": "approved", "note": "Ready for defense."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "approved"
    assert body["decision_note"] == "Ready for defense."

    # Frozen: a second decision without reopening is rejected.
    blocked = client.post(f"/check-runs/{check_run_id}/decision", json={"decision": "rejected"})
    assert blocked.status_code == 409

    # A reopen with no reason fails validation before it ever reaches the
    # service's own ConflictError path.
    bad_reopen = client.post(f"/check-runs/{check_run_id}/reopen", json={"reason": ""})
    assert bad_reopen.status_code == 422

    reopened = client.post(
        f"/check-runs/{check_run_id}/reopen", json={"reason": "Adviser asked for a second look."}
    )
    assert reopened.status_code == 200
    assert reopened.json()["decision"] is None

    redecided = client.post(f"/check-runs/{check_run_id}/decision", json={"decision": "returned"})
    assert redecided.status_code == 200
    assert redecided.json()["decision"] == "returned"
