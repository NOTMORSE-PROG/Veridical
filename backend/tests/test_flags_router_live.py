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
from app.report.service import aggregate_and_score

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_flagsapitest"


@pytest.mark.parametrize(
    "level_fields",
    [{}, {"level": None}, {"level": "high_similarity"}, {"level": "exact_duplicate"}],
)
def test_private_flag_read_retains_unclassified_support(
    logged_in_with_one_flag, api_scratch_url, level_fields
):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.enums import CheckKind
    from app.models.run import CheckResult, Flag

    client, flag_id = logged_in_with_one_flag

    async def prepare():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            async with async_sessionmaker(engine)() as session:
                flag = await session.get(Flag, flag_id)
                result = await session.get(CheckResult, flag.check_result_id)
                result.kind = CheckKind.originality_reuse
                flag.page_anchor = "whole document"
                flag.detail = {
                    "kind": "reuse_exact_duplicate",
                    "affects_readiness": False,
                    "supporting_passage": {
                        "own_text": "Own excerpt",
                        "own_context_text": "Before own. Own excerpt After own.",
                        "matched_text": "Stored excerpt",
                        "matched_context_text": "Before stored. Stored excerpt After stored.",
                        "matched_manuscript_id": 13,
                        "matched_group_label": "Foreign private identity",
                        "similarity": 0.2,
                        **level_fields,
                    },
                }
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(prepare())
    response = client.get(f"/flags/{flag_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["passage_pair"]["level"] == level_fields.get("level")
    assert body["passage_pair"]["own_excerpt"] == "Own excerpt"
    assert body["passage_pair"]["matched_context_after"] == "After stored."
    assert body["passage_pair"]["matched_ref"] == 13
    assert body["evidence_unavailable"] is False
    assert body["page_anchor"] == "whole document" and body["affects_readiness"] is False
    assert "Foreign private identity" not in response.text
    client.post("/auth/logout")
    assert client.get(f"/flags/{flag_id}").status_code == 401


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
                await aggregate_and_score(session, check_run.id, commit=True)
                return flag.id
        finally:
            await engine.dispose()

    flag_id = asyncio.run(seed())
    client.post("/auth/login", json={"email": "flags@tip.edu.ph", "password": "s3cret!"})
    return client, flag_id


@pytest.fixture()
def logged_in_with_one_confirmable_citation_flag(client, api_scratch_url):
    """BUG-078: a real `unverifiable_not_found` flag with a matching
    `citation_cache` row -- the HTTP-boundary counterpart to
    `test_flags_live.py`'s `_seed_confirmable_citation_flag`."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.citation_cache import CitationCache
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
                        "TRUNCATE audit_log, citation_cache, flag, readiness_report, "
                        "check_result, check_run, rubric, manuscript, instructor "
                        "RESTART IDENTITY CASCADE"
                    )
                )
                instructor = Instructor(
                    email="confirm@tip.edu.ph",
                    display_name="Confirm Prof",
                    password_hash=hash_password("s3cret!"),
                )
                session.add(instructor)
                await session.commit()
                manuscript = Manuscript(
                    instructor_id=instructor.id, group_label="G-12", file_ref="x.pdf"
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
                    outcome=ResultOutcome.passed,
                    score=None,
                    detail={},
                )
                session.add(result)
                await session.commit()
                session.add(
                    CitationCache(
                        key_kind="doi",
                        key_value="10.9999/router-test-source",
                        provider="crossref",
                        result={"found": False, "provider": "crossref"},
                    )
                )
                await session.commit()
                flag = Flag(
                    check_result_id=result.id,
                    severity=FlagSeverity.low,
                    evidence_excerpt="A local source not indexed by CrossRef.",
                    page_anchor="reference #1",
                    detail={
                        "kind": "unverifiable_not_found",
                        "reason": "Could not find this source in CrossRef, Semantic Scholar, "
                        "Open Library, or Google Books.",
                        "key_kind": "doi",
                        "key_value": "10.9999/router-test-source",
                    },
                )
                session.add(flag)
                await session.commit()
                await aggregate_and_score(session, check_run.id, commit=True)
                return flag.id
        finally:
            await engine.dispose()

    flag_id = asyncio.run(seed())
    client.post("/auth/login", json={"email": "confirm@tip.edu.ph", "password": "s3cret!"})
    return client, flag_id


def test_get_flag_requires_auth(client):
    assert client.get("/flags/1").status_code == 401


def test_annotate_requires_auth(client):
    assert client.post("/flags/1/annotate", json={"annotation": "x"}).status_code == 401


def test_override_requires_auth(client):
    assert client.post("/flags/1/override", json={"reason": "x"}).status_code == 401


def observed_revision(client, flag_id):
    response = client.get(f"/flags/{flag_id}")
    assert response.status_code == 200
    revision = response.json()["assessment_revision"]
    assert type(revision) is int and revision >= 0
    return revision


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
        f"/flags/{flag_id}/override",
        json={
            "reason": "Checked the source myself.",
            "expected_revision": observed_revision(client, flag_id),
        },
    )
    assert overridden.status_code == 200
    body = overridden.json()
    assert body["overridden"] is True
    assert body["override_reason"] == "Checked the source myself."
    assert "report" in body and "status" in body["report"]


def test_override_without_a_reason_is_rejected_by_the_schema(logged_in_with_one_flag):
    client, flag_id = logged_in_with_one_flag
    resp = client.post(
        f"/flags/{flag_id}/override",
        json={
            "reason": "",
            "expected_revision": observed_revision(client, flag_id),
        },
    )
    assert resp.status_code == 422


def test_annotate_without_text_is_rejected_by_the_schema(logged_in_with_one_flag):
    client, flag_id = logged_in_with_one_flag
    resp = client.post(f"/flags/{flag_id}/annotate", json={"annotation": ""})
    assert resp.status_code == 422


def test_confirm_source_requires_auth(client):
    assert client.post("/flags/1/confirm-source", json={"reason": "x"}).status_code == 401


def test_confirm_source_happy_path(logged_in_with_one_confirmable_citation_flag):
    client, flag_id = logged_in_with_one_confirmable_citation_flag
    resp = client.post(
        f"/flags/{flag_id}/confirm-source",
        json={
            "reason": "Verified on the publisher's own website.",
            "expected_revision": observed_revision(client, flag_id),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overridden"] is True
    assert body["confirmed_citation_source"] is True
    assert body["override_reason"] == "Verified on the publisher's own website."
    assert "report" in body and "status" in body["report"]


def test_confirm_source_without_a_reason_is_rejected_by_the_schema(
    logged_in_with_one_confirmable_citation_flag,
):
    client, flag_id = logged_in_with_one_confirmable_citation_flag
    resp = client.post(
        f"/flags/{flag_id}/confirm-source",
        json={
            "reason": "",
            "expected_revision": observed_revision(client, flag_id),
        },
    )
    assert resp.status_code == 422


def test_confirm_source_whitespace_only_reason_is_rejected_by_the_schema(
    logged_in_with_one_confirmable_citation_flag,
):
    """backend review (BUG-078 review), live-reproduced: `min_length=1`
    alone let a whitespace-only reason ("   ") through as a valid,
    non-empty string -- a durable, cross-manuscript, un-undoable
    confirmation must not be recordable with an effectively-blank
    justification. Confirms the 422 fires at the HTTP boundary, and that
    nothing was mutated as a side effect of the rejected request."""
    client, flag_id = logged_in_with_one_confirmable_citation_flag
    resp = client.post(
        f"/flags/{flag_id}/confirm-source",
        json={
            "reason": "   ",
            "expected_revision": observed_revision(client, flag_id),
        },
    )
    assert resp.status_code == 422

    # Confirmed it never applied -- not just that it raised.
    got = client.get(f"/flags/{flag_id}")
    assert got.json()["overridden"] is False
    assert got.json()["confirmed_citation_source"] is False


def test_confirm_source_on_a_flag_with_nothing_to_confirm_is_a_409(logged_in_with_one_flag):
    """The `logged_in_with_one_flag` fixture's flag has no `detail` at all
    (not a citation-integrity finding this endpoint applies to) --
    reachable by an ordinary client through an ordinary mistake (wrong
    flag id), not just a theoretical path."""
    client, flag_id = logged_in_with_one_flag
    resp = client.post(
        f"/flags/{flag_id}/confirm-source",
        json={
            "reason": "I checked it.",
            "expected_revision": observed_revision(client, flag_id),
        },
    )
    assert resp.status_code == 409


def test_confirm_finding_requires_auth(client):
    response = client.post("/flags/1/confirm-finding", json={"expected_revision": 0})
    assert response.status_code == 401


def test_confirming_a_finding_keeps_it_counted_and_replaces_a_dismissal(logged_in_with_one_flag):
    """BUG-518: the instructor agrees the finding is real; no note needed."""
    client, flag_id = logged_in_with_one_flag
    confirmed = client.post(
        f"/flags/{flag_id}/confirm-finding",
        json={"expected_revision": observed_revision(client, flag_id)},
    )
    assert confirmed.status_code == 200
    body = confirmed.json()
    assert body["finding_confirmed"] is True and body["overridden"] is False
    assert body["finding_confirm_note"] is None and body["finding_confirmed_at"]
    assert "report" in body
    summary = client.get(f"/check-runs/{body['check_run_id']}/flags").json()
    assert next(row for row in summary if row["id"] == flag_id)["finding_confirmed"] is True

    dismissed = client.post(
        f"/flags/{flag_id}/override",
        json={"reason": "Checked again.", "expected_revision": observed_revision(client, flag_id)},
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["finding_confirmed"] is False
    assert dismissed.json()["overridden"] is True

    again = client.post(
        f"/flags/{flag_id}/confirm-finding",
        json={"note": "  It is real.  ", "expected_revision": observed_revision(client, flag_id)},
    )
    assert again.status_code == 200
    assert again.json()["finding_confirm_note"] == "It is real."
    assert again.json()["overridden"] is False and again.json()["override_reason"] is None


def test_confirming_with_a_stale_revision_is_refused(logged_in_with_one_flag):
    client, flag_id = logged_in_with_one_flag
    stale = observed_revision(client, flag_id) + 5
    response = client.post(f"/flags/{flag_id}/confirm-finding", json={"expected_revision": stale})
    assert response.status_code == 409
