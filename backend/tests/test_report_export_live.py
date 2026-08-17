"""V-039: GET /check-runs/{id}/report/export.pdf -- the report PDF
export's HTTP plumbing (auth, ownership, headers, real PDF bytes) and
the archive-size disclosure's data assembly. The document's actual
visual layout is exercised by the ui-designer-specced content, not
asserted here -- this file proves the endpoint returns a REAL,
non-empty PDF built from the SAME data the on-screen report/flags
panels use, never a second divergent read path.
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

SCRATCH_DB = "veridical_reportexportapitest"


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
                    instructor_id=instructor.id, group_label="Piñas ni Niño", file_ref="x.pdf"
                )
                rubric = Rubric(
                    instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=True
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
                # F7's own archive-size disclosure (V-037) -- criterion_id
                # is None, exactly like the real reuse check writes it.
                session.add(
                    CheckResult(
                        check_run_id=check_run.id,
                        criterion_id=None,
                        kind=CheckKind.originality_reuse,
                        outcome=ResultOutcome.passed,
                        detail={"archive_size_n": 7, "n_flags": 0},
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


def test_export_requires_auth(client):
    resp = client.get("/check-runs/1/report/export.pdf")
    assert resp.status_code == 401


def test_export_returns_a_real_pdf_with_the_right_headers(logged_in_with_a_done_run):
    client, check_run_id = logged_in_with_a_done_run
    resp = client.get(f"/check-runs/{check_run_id}/report/export.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert f"veridical-report-{check_run_id}.pdf" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF")
    assert len(resp.content) > 0


def test_export_formats_a_pending_criterions_weight_the_same_way_everywhere(
    client, api_scratch_url
):
    """ux-critic finding (P2, live-reproduced): the Criteria Results table
    rounded weight to 1 decimal (matching Report.tsx's own formatWeight())
    while the pending-escalation section printed the raw unrounded value
    -- the SAME fact read as two different numbers in one document."""
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
                    instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=True
                )
                session.add_all([manuscript, rubric])
                await session.commit()
                # 1/7 of 100 = 14.285714...% -- rounds to 14.3%, never 14.286%.
                criterion = Criterion(
                    rubric_id=rubric.id,
                    type="semantic",
                    text="Chapter 1 states the research problem",
                    evidence=None,
                    weight=Decimal("14.285714"),
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
                        kind=CheckKind.semantic,
                        outcome=ResultOutcome.escalated,
                        detail={"reason": "Split vote"},
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

    resp = client.get(f"/check-runs/{check_run_id}/report/export.pdf")
    assert resp.status_code == 200

    import fitz

    doc = fitz.open(stream=resp.content, filetype="pdf")
    full_text = "\n".join(page.get_text() for page in doc)
    assert "14.3%" in full_text
    assert "14.286%" not in full_text
    assert "14.285714%" not in full_text


def test_export_survives_real_ampersand_bearing_citation_text(client, api_scratch_url):
    """ui-designer finding: a real seeded flag reads 'Reyes, J. P., & Cruz,
    M. A. (2023)...' -- reportlab's Paragraph parses its input as a mini
    XML/HTML subset, so an un-escaped '&'/'<'/'>' in ANY manuscript-
    derived string (citation excerpts, criterion text, decision notes)
    throws a parse error or corrupts output. Not hypothetical: this
    exact character is in the live dev database today."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.enums import CheckKind, CheckRunStatus, FlagSeverity, ResultOutcome
    from app.models.instructor import Instructor
    from app.models.manuscript import Manuscript
    from app.models.rubric import Criterion, Rubric
    from app.models.run import CheckResult, CheckRun, Flag
    from app.report.service import aggregate_and_score

    async def seed():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    text(
                        "TRUNCATE flag, readiness_report, check_result, check_run, criterion, "
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
                    instructor_id=instructor.id, group_label="G <Test> & Co", file_ref="x.pdf"
                )
                rubric = Rubric(
                    instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=True
                )
                session.add_all([manuscript, rubric])
                await session.commit()
                criterion = Criterion(
                    rubric_id=rubric.id,
                    type="structural",
                    text="Cites <em>at least</em> 5 & 10 sources",
                    evidence=None,
                    weight=Decimal("100"),
                    position=0,
                )
                session.add(criterion)
                await session.commit()
                check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
                session.add(check_run)
                await session.commit()
                structural_result = CheckResult(
                    check_run_id=check_run.id,
                    criterion_id=criterion.id,
                    kind=CheckKind.structural,
                    outcome=ResultOutcome.passed,
                    score=100.0,
                    detail={"rule_id": "citation_count", "anchor": "page 2"},
                )
                citation_result = CheckResult(
                    check_run_id=check_run.id,
                    criterion_id=None,
                    kind=CheckKind.citation_integrity,
                    outcome=ResultOutcome.passed,
                )
                session.add_all([structural_result, citation_result])
                await session.commit()
                session.add(
                    Flag(
                        check_result_id=citation_result.id,
                        severity=FlagSeverity.high,
                        evidence_excerpt="Reyes, J. P., & Cruz, M. A. (2023) <Journal> A & B.",
                        page_anchor="p. 12 & 13",
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

    resp = client.get(f"/check-runs/{check_run_id}/report/export.pdf")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")

    import fitz

    doc = fitz.open(stream=resp.content, filetype="pdf")
    full_text = "\n".join(page.get_text() for page in doc)
    assert "G <Test> & Co" in full_text
    assert "Reyes, J. P., & Cruz, M. A. (2023) <Journal> A & B." in full_text


def test_export_marks_an_undecided_report_draft_with_the_watermark_and_footer_line(
    logged_in_with_a_done_run,
):
    client, check_run_id = logged_in_with_a_done_run
    resp = client.get(f"/check-runs/{check_run_id}/report/export.pdf")
    assert resp.status_code == 200

    import fitz

    doc = fitz.open(stream=resp.content, filetype="pdf")
    full_text = "\n".join(page.get_text() for page in doc)
    assert "DRAFT" in full_text
    assert "no final decision yet" in full_text
    assert "not an automated grade" in full_text


def test_export_discloses_unknown_llm_mode_distinctly_from_real(logged_in_with_a_done_run):
    """backend-critic finding (BUG-049 review, live-reproduced against
    report/29 -- the ticket's own reproduction case): a run that
    predates `llm_mode` tracking backfills to "unknown", and treating
    that the same as "real" (i.e. showing nothing) silently recreates
    the exact non-disclosure this ticket exists to close, for every
    pre-migration run. `logged_in_with_a_done_run` seeds a plain
    `CheckRun(...)` with no explicit `llm_mode` -- exactly the
    server_default backfill shape."""
    client, check_run_id = logged_in_with_a_done_run
    resp = client.get(f"/check-runs/{check_run_id}/report/export.pdf")
    assert resp.status_code == 200

    import fitz

    doc = fitz.open(stream=resp.content, filetype="pdf")
    full_text = "\n".join(page.get_text() for page in doc)
    assert "AI mode unknown" in full_text
    assert "can't be confirmed" in full_text
    assert "Test-mode run" not in full_text


def test_export_discloses_test_mode_run(client, api_scratch_url):
    """BUG-049: a fake-LLM-mode run's exported PDF used to look
    IDENTICAL to a real one -- no disclosure anywhere a reader looks.
    The disclosure must survive to the printed artifact, not just the
    on-screen report."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.enums import CheckKind, CheckRunStatus, LLMMode, ResultOutcome
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
                    instructor_id=instructor.id, group_label="G", file_ref="x.pdf"
                )
                rubric = Rubric(
                    instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=True
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
                check_run = CheckRun(
                    manuscript_id=manuscript.id, rubric_id=rubric.id, llm_mode=LLMMode.fake
                )
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

    resp = client.get(f"/check-runs/{check_run_id}/report/export.pdf")
    assert resp.status_code == 200

    import fitz

    doc = fitz.open(stream=resp.content, filetype="pdf")
    full_text = "\n".join(page.get_text() for page in doc)
    assert "Test-mode run" in full_text
    assert "AI results are simulated" in full_text


def test_export_rejects_a_strangers_check_run(logged_in_with_a_done_run, api_scratch_url):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.instructor import Instructor

    async def seed_stranger():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine)
            async with factory() as session:
                session.add(
                    Instructor(
                        email="stranger@tip.edu.ph",
                        display_name="Stranger",
                        password_hash=hash_password("other!"),
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    client, check_run_id = logged_in_with_a_done_run
    asyncio.run(seed_stranger())

    client.post("/auth/logout")
    client.post("/auth/login", json={"email": "stranger@tip.edu.ph", "password": "other!"})
    resp = client.get(f"/check-runs/{check_run_id}/report/export.pdf")
    assert resp.status_code == 404
