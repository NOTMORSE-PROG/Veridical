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
                await aggregate_and_score(session, check_run.id, commit=True)
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


def _set_run_original(url, run_id, path, extraction=None):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.manuscript import Manuscript
    from app.models.run import CheckRun

    async def update():
        engine = create_async_engine(sqlalchemy_url(url))
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                run = await session.get(CheckRun, run_id)
                manuscript = await session.get(Manuscript, run.manuscript_id)
                manuscript.file_ref = str(path)
                manuscript.original_filename = path.name
                if extraction is not None:
                    manuscript.content_hash = extraction.docx_source_sha256
                    manuscript.section_tree = extraction.section_tree.model_dump(mode="json")
                await session.commit()
                return manuscript.id
        finally:
            await engine.dispose()

    return asyncio.run(update())


@pytest.mark.parametrize("kind,suffix", [("pdf", ".docx"), ("pdf", ".txt"), ("docx", ".pdf")])
def test_authenticated_source_routes_follow_bytes(
    logged_in_with_a_done_run, api_scratch_url, tmp_path, kind, suffix
):
    import hashlib

    from app.ingest.docx import extract_document
    from app.ingest.service import _write_raw_store, raw_store_path
    from tests.test_ingest_fixtures import FIXTURE_DIR

    client, run_id = logged_in_with_a_done_run
    path = tmp_path / f"paper{suffix}"
    payload = (FIXTURE_DIR / ("image_table.pdf" if kind == "pdf" else "native.docx")).read_bytes()
    path.write_bytes(payload)
    extraction = extract_document(str(path), get_settings()) if kind == "docx" else None
    mid = _set_run_original(api_scratch_url, run_id, path, extraction)
    if extraction is not None:
        raw = raw_store_path(get_settings(), mid)
        _write_raw_store(extraction, raw)
        before = hashlib.sha256(raw.read_bytes()).hexdigest()
    viewer = client.get(f"/check-runs/{run_id}/document")
    assert viewer.status_code == 200, viewer.text
    assert viewer.json()["source_format"] == kind and viewer.json()["available"] is True
    file = client.get(f"/check-runs/{run_id}/document/file")
    paragraphs = client.get(f"/check-runs/{run_id}/document/paragraphs")
    if kind == "pdf":
        assert viewer.json()["page_count"] == 2
        assert file.status_code == 200 and file.content == payload
        assert file.headers["content-type"].startswith("application/pdf")
        assert file.headers["cache-control"] == "private, no-store"
        assert file.headers["x-content-type-options"] == "nosniff"
        assert paragraphs.status_code == 409
    else:
        assert file.status_code == 409
        assert paragraphs.status_code == 200 and paragraphs.json()["paragraphs"]
        assert hashlib.sha256(raw.read_bytes()).hexdigest() == before
    assert path.read_bytes() == payload
    client.post("/auth/logout")
    for route in ("document", "document/file", "document/paragraphs"):
        assert client.get(f"/check-runs/{run_id}/{route}").status_code == 401


def test_authenticated_source_rejects_corrupt_pdf(
    logged_in_with_a_done_run, api_scratch_url, tmp_path
):
    client, run_id = logged_in_with_a_done_run
    path = tmp_path / "broken.docx"
    path.write_bytes(b"%PDF-1.4 broken")
    _set_run_original(api_scratch_url, run_id, path)
    response = client.get(f"/check-runs/{run_id}/document")
    assert response.status_code == 200 and response.json()["available"] is False
    assert client.get(f"/check-runs/{run_id}/document/file").status_code == 409


def _configure_saved_native_coverage(url, run_id, suffix, detail, purged):
    import asyncio
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.enums import CheckKind, ResultOutcome
    from app.models.manuscript import Manuscript
    from app.models.run import CheckResult, CheckRun
    from app.report.service import aggregate_and_score

    async def setup():
        engine = create_async_engine(sqlalchemy_url(url))
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                run = await session.get(CheckRun, run_id)
                manuscript = await session.get(Manuscript, run.manuscript_id)
                manuscript.file_ref = f"original-no-longer-present{suffix}"
                manuscript.purged_at = datetime.now(UTC) if purged else None
                if detail is not None:
                    limited = detail.get("native_math_inventory_state") in {
                        "not_recorded",
                        "unavailable",
                    } or bool(detail.get("n_native_equations_unassessed"))
                    session.add(
                        CheckResult(
                            check_run_id=run_id,
                            kind=CheckKind.statistical_forensics,
                            outcome=ResultOutcome.unverifiable
                            if limited
                            else ResultOutcome.not_applicable,
                            detail=detail,
                        )
                    )
                await session.commit()
                await aggregate_and_score(session, run_id, commit=True)
        finally:
            await engine.dispose()

    asyncio.run(setup())


def _saved_assessment_rows(url):
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db import sqlalchemy_url

    async def snapshot():
        engine = create_async_engine(sqlalchemy_url(url))
        try:
            async with engine.connect() as connection:
                return {
                    table: await connection.scalar(
                        text(
                            "SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY id), '[]'::jsonb) "
                            f"FROM {table} t"
                        )
                    )
                    for table in (
                        "check_run",
                        "check_result",
                        "flag",
                        "readiness_report",
                        "rubric",
                        "criterion",
                        "manuscript",
                        "audit_log",
                    )
                }
        finally:
            await engine.dispose()

    return asyncio.run(snapshot())


@pytest.mark.parametrize("suffix", [".pdf", ".docx"])
@pytest.mark.parametrize(
    "detail,purged,expected",
    [
        pytest.param(None, False, True, id="no-recorded-check"),
        pytest.param({}, False, True, id="legacy-check-no-marker"),
        pytest.param({"native_math_coverage_version": "v0"}, False, True, id="old-marker"),
        pytest.param(None, True, True, id="purged-unknown-record"),
        pytest.param(
            {
                "native_math_coverage_version": "v1",
                "native_math_inventory_state": "complete",
                "n_native_equations_unassessed": 0,
            },
            False,
            False,
            id="recorded-zero",
        ),
        pytest.param(
            {
                "native_math_coverage_version": "v1",
                "native_math_inventory_state": "complete",
                "n_native_equations_unassessed": 2,
                "coverage_reason": "native_math_unassessed",
                "analysis_coverage": "unsupported",
                "assessment_scope": "available_text_and_tables",
            },
            False,
            False,
            id="recorded-positive-unassessed",
        ),
        pytest.param(
            {
                "native_math_coverage_version": "v1",
                "native_math_inventory_state": "not_applicable",
                "n_native_equations_unassessed": 0,
            },
            True,
            False,
            id="purged-recorded-not-applicable",
        ),
        pytest.param(
            {
                "native_math_coverage_version": "v1",
                "native_math_inventory_state": "not_recorded",
                "n_native_equations_unassessed": None,
                "coverage_reason": "native_math_inventory_unavailable",
                "analysis_coverage": "unsupported",
                "assessment_scope": "available_text_and_tables",
            },
            False,
            False,
            id="current-unrecorded-inventory",
        ),
        pytest.param(
            {
                "native_math_coverage_version": "v1",
                "native_math_inventory_state": "unavailable",
                "n_native_equations_unassessed": None,
                "coverage_reason": "native_math_inventory_unavailable",
                "analysis_coverage": "unsupported",
                "assessment_scope": "available_text_and_tables",
            },
            False,
            False,
            id="current-unavailable-inventory",
        ),
    ],
)
def test_saved_native_coverage_uses_recorded_metadata_without_original_reads_or_rewrites(
    logged_in_with_a_done_run, api_scratch_url, monkeypatch, suffix, detail, purged, expected
):
    client, run_id = logged_in_with_a_done_run
    _configure_saved_native_coverage(api_scratch_url, run_id, suffix, detail, purged)
    before = _saved_assessment_rows(api_scratch_url)

    def original_read_forbidden(*args, **kwargs):
        pytest.fail("Saved coverage projection must not inspect an original source")

    monkeypatch.setattr("app.report.service.manuscript_source_file", original_read_forbidden)
    response = client.get(f"/check-runs/{run_id}/report")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["historical_native_math_unverified"] is expected
    if detail and detail.get("coverage_reason"):
        current = next(
            row
            for row in body["integrity_check_status"]
            if row["check_kind"] == "statistical_forensics"
        )
        assert current["coverage_reason"] == detail["coverage_reason"]
        assert current["native_math_inventory_state"] == detail["native_math_inventory_state"]
        assert current["n_native_equations_unassessed"] == detail["n_native_equations_unassessed"]
    assert _saved_assessment_rows(api_scratch_url) == before


def test_decide_then_reopen_over_http(logged_in_with_a_done_run):
    client, check_run_id = logged_in_with_a_done_run
    resp = client.post(
        f"/check-runs/{check_run_id}/decision",
        json={"decision": "approved", "note": "Ready for defense.", "expected_revision": 0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "approved"
    assert body["decision_note"] == "Ready for defense."

    # Frozen: a second decision without reopening is rejected.
    blocked = client.post(
        f"/check-runs/{check_run_id}/decision",
        json={"decision": "rejected", "expected_revision": 1},
    )
    assert blocked.status_code == 409

    # A reopen with no reason fails validation before it ever reaches the
    # service's own ConflictError path.
    bad_reopen = client.post(f"/check-runs/{check_run_id}/reopen", json={"reason": ""})
    assert bad_reopen.status_code == 422

    reopened = client.post(
        f"/check-runs/{check_run_id}/reopen",
        json={"reason": "Adviser asked for a second look.", "expected_revision": 1},
    )
    assert reopened.status_code == 200
    assert reopened.json()["decision"] is None

    redecided = client.post(
        f"/check-runs/{check_run_id}/decision",
        json={"decision": "returned", "expected_revision": 2},
    )
    assert redecided.status_code == 200
    assert redecided.json()["decision"] == "returned"
