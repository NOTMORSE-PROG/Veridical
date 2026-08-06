"""V-026 HTTP surface smoke test: GET/POST /flags/{id}/..., auth-gated,
same convention as test_audit_router_live.py. Business logic is covered
at the service layer (test_flags_live.py) — this only proves the HTTP
wiring (auth guard, status codes, response shape, mandatory-reason
validation at the router's own request schema).
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

SCRATCH_DB = "veridical_flagsapitest"


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
def logged_in_with_one_flag(client, api_scratch_url):
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.enums import CheckKind, CheckRunStatus, FlagSeverity, ResultOutcome
    from app.models.instructor import Instructor
    from app.models.manuscript import Manuscript
    from app.models.rubric import Rubric
    from app.models.run import CheckResult, CheckRun, Flag

    async def seed():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    text(
                        "TRUNCATE audit_log, flag, readiness_report, check_result, "
                        "check_run, rubric, manuscript, instructor RESTART IDENTITY CASCADE"
                    )
                )
                instructor = Instructor(
                    email="flags@tip.edu.ph",
                    display_name="Flags Prof",
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
                check_run = CheckRun(
                    manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
                )
                session.add(check_run)
                await session.commit()
                result = CheckResult(
                    check_run_id=check_run.id,
                    criterion_id=None,
                    kind=CheckKind.citation_integrity,
                    outcome=ResultOutcome.failed,
                    score=None,
                    detail={"basis": "external-api", "verdict": "not_supported"},
                )
                session.add(result)
                await session.commit()
                flag = Flag(
                    check_result_id=result.id,
                    severity=FlagSeverity.med,
                    confidence=Decimal("0.667"),
                    evidence_excerpt="Some claim.",
                    page_anchor="page 12",
                )
                session.add(flag)
                await session.commit()
                return flag.id
        finally:
            await engine.dispose()

    flag_id = asyncio.run(seed())
    client.post("/auth/login", json={"email": "flags@tip.edu.ph", "password": "s3cret!"})
    return client, flag_id


def test_get_flag_requires_auth(client):
    assert client.get("/flags/1").status_code == 401


def test_annotate_requires_auth(client):
    assert client.post("/flags/1/annotate", json={"annotation": "x"}).status_code == 401


def test_override_requires_auth(client):
    assert client.post("/flags/1/override", json={"reason": "x"}).status_code == 401


def test_get_annotate_override_happy_path(logged_in_with_one_flag):
    client, flag_id = logged_in_with_one_flag

    got = client.get(f"/flags/{flag_id}")
    assert got.status_code == 200
    assert got.json()["overridden"] is False
    # Needed to build a breadcrumb back to the report from a direct/
    # bookmarked link (screen 4i, V-055 review — previously missing).
    assert got.json()["manuscript_group_label"] == "G-11"
    assert isinstance(got.json()["check_run_id"], int)

    annotated = client.post(f"/flags/{flag_id}/annotate", json={"annotation": "Looks off."})
    assert annotated.status_code == 200
    assert annotated.json()["annotation"] == "Looks off."

    overridden = client.post(
        f"/flags/{flag_id}/override", json={"reason": "Checked the source myself."}
    )
    assert overridden.status_code == 200
    body = overridden.json()
    assert body["overridden"] is True
    assert body["override_reason"] == "Checked the source myself."
    assert "report" in body and "status" in body["report"]


def test_override_without_a_reason_is_rejected_by_the_schema(logged_in_with_one_flag):
    client, flag_id = logged_in_with_one_flag
    resp = client.post(f"/flags/{flag_id}/override", json={"reason": ""})
    assert resp.status_code == 422


def test_annotate_without_text_is_rejected_by_the_schema(logged_in_with_one_flag):
    client, flag_id = logged_in_with_one_flag
    resp = client.post(f"/flags/{flag_id}/annotate", json={"annotation": ""})
    assert resp.status_code == 422
