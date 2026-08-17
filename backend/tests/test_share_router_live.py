"""V-040: instructor-side share-link management (screens 4k) and the
public, unauthenticated adviser view (screen 4l). Needs a live Postgres
(same convention as test_report_router_live.py).
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

SCRATCH_DB = "veridical_shareapitest"


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
                        "TRUNCATE share_link, readiness_report, check_result, check_run, "
                        "criterion, rubric, manuscript, instructor RESTART IDENTITY CASCADE"
                    )
                )
                owner = Instructor(
                    email="prof@tip.edu.ph",
                    display_name="Prof",
                    password_hash=hash_password("s3cret!"),
                )
                stranger = Instructor(
                    email="stranger@tip.edu.ph",
                    display_name="Stranger",
                    password_hash=hash_password("other!"),
                )
                session.add_all([owner, stranger])
                await session.commit()
                manuscript = Manuscript(
                    instructor_id=owner.id, group_label="G-11", file_ref="x.pdf"
                )
                rubric = Rubric(
                    instructor_id=owner.id, title="Format", source_file="r.pdf", is_active=True
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


def test_share_management_requires_auth(client):
    assert client.get("/check-runs/1/share").status_code == 401
    assert client.post("/check-runs/1/share", json={}).status_code == 401
    assert client.delete("/check-runs/1/share").status_code == 401


def test_no_active_link_by_default(logged_in_with_a_done_run):
    client, check_run_id = logged_in_with_a_done_run
    resp = client.get(f"/check-runs/{check_run_id}/share")
    assert resp.status_code == 200
    assert resp.json() is None


def test_creating_a_link_makes_it_active_and_the_shared_report_reachable(
    logged_in_with_a_done_run,
):
    client, check_run_id = logged_in_with_a_done_run
    created = client.post(f"/check-runs/{check_run_id}/share", json={})
    assert created.status_code == 200
    token = created.json()["token"]
    assert len(token) >= 32  # secrets.token_urlsafe(32) -- comfortably >=128 bits

    active = client.get(f"/check-runs/{check_run_id}/share")
    assert active.status_code == 200
    assert active.json()["token"] == token

    # The public route must work with NO session cookie at all -- same
    # TestClient (a separate instance would spawn a second event loop,
    # incompatible with the shared app's loop-bound asyncpg engine on
    # Windows, per this project's own established test-fixture precedent),
    # cookie cleared instead. The route has no auth dependency in its
    # signature at all, so this is a real "logged out" request, not just
    # an unused cookie sitting there.
    client.cookies.clear()
    shared = client.get(f"/shared/{token}/report")
    assert shared.status_code == 200
    assert shared.json()["report"]["check_run_id"] == check_run_id
    assert shared.headers["x-robots-tag"] == "noindex, nofollow"


def test_regenerating_replaces_the_token_and_kills_the_old_one(logged_in_with_a_done_run):
    client, check_run_id = logged_in_with_a_done_run
    first = client.post(f"/check-runs/{check_run_id}/share", json={}).json()["token"]
    second = client.post(f"/check-runs/{check_run_id}/share", json={}).json()["token"]
    assert first != second

    client.cookies.clear()
    old = client.get(f"/shared/{first}/report")
    assert old.status_code == 410
    new = client.get(f"/shared/{second}/report")
    assert new.status_code == 200


def test_revoking_returns_410_not_a_generic_404(logged_in_with_a_done_run):
    client, check_run_id = logged_in_with_a_done_run
    token = client.post(f"/check-runs/{check_run_id}/share", json={}).json()["token"]

    revoke = client.delete(f"/check-runs/{check_run_id}/share")
    assert revoke.status_code == 200

    after = client.get(f"/check-runs/{check_run_id}/share")
    assert after.json() is None

    client.cookies.clear()
    resp = client.get(f"/shared/{token}/report")
    assert resp.status_code == 410


def test_revoking_with_nothing_active_is_a_clear_error_not_a_silent_noop(
    logged_in_with_a_done_run,
):
    client, check_run_id = logged_in_with_a_done_run
    resp = client.delete(f"/check-runs/{check_run_id}/share")
    assert resp.status_code == 404


def test_an_unknown_token_is_404_not_410(logged_in_with_a_done_run):
    client, _check_run_id = logged_in_with_a_done_run
    client.cookies.clear()
    resp = client.get("/shared/this-token-was-never-issued/report")
    assert resp.status_code == 404


def test_a_stranger_cannot_manage_another_instructors_share_link(logged_in_with_a_done_run):
    client, check_run_id = logged_in_with_a_done_run
    client.post("/auth/logout")
    client.post("/auth/login", json={"email": "stranger@tip.edu.ph", "password": "other!"})

    assert client.get(f"/check-runs/{check_run_id}/share").status_code == 404
    assert client.post(f"/check-runs/{check_run_id}/share", json={}).status_code == 404
    assert client.delete(f"/check-runs/{check_run_id}/share").status_code == 404


def test_an_expired_link_is_410(logged_in_with_a_done_run):
    client, check_run_id = logged_in_with_a_done_run
    created = client.post(
        f"/check-runs/{check_run_id}/share",
        json={"expires_at": "2020-01-01T00:00:00Z"},
    )
    assert created.status_code == 200
    token = created.json()["token"]

    client.cookies.clear()
    resp = client.get(f"/shared/{token}/report")
    assert resp.status_code == 410


def test_shared_report_exposes_zero_mutating_endpoints(logged_in_with_a_done_run):
    """AC: the adviser view must expose zero mutating endpoints -- the
    ENTIRE public surface is one GET route; nothing to POST/PUT/DELETE
    reaches this data at all without a real instructor session."""
    client, check_run_id = logged_in_with_a_done_run
    client.post(f"/check-runs/{check_run_id}/share", json={})

    client.cookies.clear()
    for method, path in [
        ("post", f"/check-runs/{check_run_id}/decision"),
        ("post", f"/check-runs/{check_run_id}/reopen"),
        ("post", f"/check-runs/{check_run_id}/escalated/1/resolve"),
        ("get", f"/check-runs/{check_run_id}/escalated"),
        ("get", "/audit"),
        ("get", "/stats"),
    ]:
        resp = getattr(client, method)(path, json={}) if method == "post" else client.get(path)
        assert resp.status_code == 401, (
            f"{method.upper()} {path} should require auth, got {resp.status_code}"
        )
    # The token itself grants no access to any OTHER endpoint either.
    assert client.get(f"/check-runs/{check_run_id}/report").status_code == 401


@pytest.fixture()
def logged_in_with_a_resolved_and_prior_run(client, api_scratch_url):
    """BUG-044 (High, live-reproduced): seeds the exact scenario that
    leaked -- a criterion the instructor resolved out of escalation
    (carries `resolution.reason`, their own private override reasoning)
    AND an earlier DONE run for the same manuscript+rubric family (so
    `previous_status`/`previous_composite_score` are real, non-null
    values, not just absent-by-coincidence). The current (second) run is
    the one shared."""
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
                        "TRUNCATE share_link, readiness_report, check_result, check_run, "
                        "criterion, rubric, manuscript, instructor RESTART IDENTITY CASCADE"
                    )
                )
                owner = Instructor(
                    email="prof2@tip.edu.ph",
                    display_name="Prof",
                    password_hash=hash_password("s3cret!"),
                )
                session.add(owner)
                await session.commit()
                manuscript = Manuscript(
                    instructor_id=owner.id, group_label="G-12", file_ref="x.pdf"
                )
                rubric = Rubric(
                    instructor_id=owner.id, title="Format", source_file="r.pdf", is_active=True
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

                # Run 1: an earlier DONE + reported run -- the "previous"
                # run this instructor never shared.
                run1 = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
                session.add(run1)
                await session.commit()
                session.add(
                    CheckResult(
                        check_run_id=run1.id,
                        criterion_id=criterion.id,
                        kind=CheckKind.structural,
                        outcome=ResultOutcome.failed,
                        score=0.0,
                        detail={"reason": "No abstract found."},
                    )
                )
                run1.status = CheckRunStatus.done
                await session.commit()
                await aggregate_and_score(session, run1.id)

                # Run 2: the run actually shared -- one criterion resolved
                # out of escalation, carrying the instructor's own
                # private override reasoning.
                run2 = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
                session.add(run2)
                await session.commit()
                session.add(
                    CheckResult(
                        check_run_id=run2.id,
                        criterion_id=criterion.id,
                        kind=CheckKind.structural,
                        outcome=ResultOutcome.passed,
                        score=100.0,
                        detail={
                            "resolution": {
                                "type": "mark_pass",
                                "reason": "Verified manually for review testing.",
                                "ai_majority_verdict": "fail",
                            }
                        },
                    )
                )
                run2.status = CheckRunStatus.done
                await session.commit()
                await aggregate_and_score(session, run2.id)
                return run2.id
        finally:
            await engine.dispose()

    check_run_id = asyncio.run(seed())
    client.post("/auth/login", json={"email": "prof2@tip.edu.ph", "password": "s3cret!"})
    return client, check_run_id


def test_shared_report_never_leaks_resolution_reasoning_or_a_prior_runs_score(
    logged_in_with_a_resolved_and_prior_run,
):
    """BUG-044 (High): live-reproduces the exact leak and proves the fix.
    Before the fix, `resolution.reason` and `previous_status`/
    `previous_composite_score` were all present in this exact response."""
    client, check_run_id = logged_in_with_a_resolved_and_prior_run

    # Confirm the leak-worthy data is REAL on the authenticated side first
    # -- this test would be meaningless if the seed never produced it.
    authed = client.get(f"/check-runs/{check_run_id}/report").json()
    assert authed["previous_status"] == "not_ready"
    assert authed["results"][0]["resolution"]["reason"] == "Verified manually for review testing."

    client.post(f"/check-runs/{check_run_id}/share", json={})
    token = client.get(f"/check-runs/{check_run_id}/share").json()["token"]

    client.cookies.clear()
    shared = client.get(f"/shared/{token}/report").json()

    assert "resolution" not in shared["report"]["results"][0]
    assert "previous_status" not in shared["report"]
    assert "previous_composite_score" not in shared["report"]
    assert "pending_review_count" not in shared["report"]

    # Belt-and-suspenders: the leaked text must not appear ANYWHERE in the
    # raw response, not just be absent from the specific key it used to
    # live under (guards against it resurfacing under a different key).
    import json as _json

    raw = _json.dumps(shared)
    assert "Verified manually for review testing" not in raw


def test_shared_report_public_schema_is_an_exact_allow_list(
    logged_in_with_a_resolved_and_prior_run,
):
    """The cheapest structural guard available (BUG-044's own
    recommendation): assert the EXACT key set, so a future field added to
    `ReportOut`/`CriterionResultOut` fails this test instead of silently
    reaching an anonymous reader the moment someone reuses that model."""
    client, check_run_id = logged_in_with_a_resolved_and_prior_run
    client.post(f"/check-runs/{check_run_id}/share", json={})
    token = client.get(f"/check-runs/{check_run_id}/share").json()["token"]

    client.cookies.clear()
    shared = client.get(f"/shared/{token}/report").json()

    assert set(shared["report"].keys()) == {
        "check_run_id",
        "manuscript_group_label",
        "manuscript_original_filename",
        "rubric_title",
        "status",
        "composite_score",
        "thresholds",
        "reason",
        "flag_deduction",
        "unresolved_high_flag_count",
        "results",
        "decision",
        "decided_at",
        "decision_note",
        "rubric_is_current",
        # BUG-049: deliberately published -- the adviser reading a shared
        # link has no other context to catch a fake-mode fixture finding
        # presented as real.
        "llm_mode",
        # BUG-052: deliberately published -- the adviser needs to know the
        # measuring instrument (the rubric) was flagged too, not just the
        # instructor.
        "rubric_needs_review",
        "rubric_parse_issues",
    }
    assert set(shared["report"]["results"][0].keys()) == {
        "criterion_id",
        "text",
        "type",
        "weight",
        # D-023: the Low/Medium/High importance bucket, deliberately
        # published too -- the adviser reads the same criteria table.
        "weight_importance",
        "kind",
        "outcome",
        "score",
        "basis",
        "anchor",
        "reasoning",
        "reason",
        "evidence",
    }
