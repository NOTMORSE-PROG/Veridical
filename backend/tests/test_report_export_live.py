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


def test_export_shows_weight_as_an_importance_label_never_a_percentage(client, api_scratch_url):
    """D-023 (BUG-051/052/098): weight is a relative value with no
    required total -- rendered as a Low/Medium/High importance label in
    BOTH the pending-escalation section and the Criteria Results table,
    never as a raw percentage (which asserted a scale it didn't have --
    ux-critic's own prior finding on this exact pair of renderers was
    that they used to format the SAME fact two different ways; the fix
    is that both now call the one shared `_format_weight_importance`,
    structurally, not just by convention)."""
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
                # Deliberately unequal weights, average = (18+2+10)/3 = 10:
                # 18 is 1.8x average -> High; 2 is 0.2x -> Low; 10 is
                # exactly 1.0x -> Medium -- three distinct labels, one in
                # the pending section (escalated), two in the criteria
                # table (decided).
                pending_criterion = Criterion(
                    rubric_id=rubric.id,
                    type="semantic",
                    text="Chapter 1 states the research problem",
                    evidence=None,
                    weight=Decimal("18"),
                    position=0,
                )
                low_criterion = Criterion(
                    rubric_id=rubric.id,
                    type="structural",
                    text="Has an abstract",
                    evidence=None,
                    weight=Decimal("2"),
                    position=1,
                )
                med_criterion = Criterion(
                    rubric_id=rubric.id,
                    type="structural",
                    text="Has a references section",
                    evidence=None,
                    weight=Decimal("10"),
                    position=2,
                )
                session.add_all([pending_criterion, low_criterion, med_criterion])
                await session.commit()
                check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
                session.add(check_run)
                await session.commit()
                session.add(
                    CheckResult(
                        check_run_id=check_run.id,
                        criterion_id=pending_criterion.id,
                        kind=CheckKind.semantic,
                        outcome=ResultOutcome.escalated,
                        detail={"reason": "Split vote"},
                    )
                )
                session.add(
                    CheckResult(
                        check_run_id=check_run.id,
                        criterion_id=low_criterion.id,
                        kind=CheckKind.structural,
                        outcome=ResultOutcome.passed,
                        score=100.0,
                        detail={"rule_id": "required_section_present", "anchor": "page 2"},
                    )
                )
                session.add(
                    CheckResult(
                        check_run_id=check_run.id,
                        criterion_id=med_criterion.id,
                        kind=CheckKind.structural,
                        outcome=ResultOutcome.passed,
                        score=100.0,
                        detail={"rule_id": "required_section_present", "anchor": "page 3"},
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
    # The pending section's own inline phrasing ("(High importance)").
    assert "High importance" in full_text
    # The criteria table's short labels for the two decided criteria.
    assert "Low" in full_text
    assert "Medium" in full_text
    # Never shown alongside the criterion as a raw weight percentage
    # (the pre-fix behavior for these exact numbers would have shown
    # "60.0%", "6.7%", "33.3%" next to the criterion text/importance).
    assert "(High importance)" in full_text


def test_export_never_drops_a_pending_criterion_from_a_decided_report(client, api_scratch_url):
    """BUG-081: `report/service.py`'s `decide_report` already refuses to
    decide a report while a criterion is still escalated/quota_exhausted/
    api_down (`test_deciding_with_unresolved_escalations_is_blocked_with_
    the_count`, `test_report_decision_live.py`) -- the specific
    reachability path the ticket described (decide with escalations
    pending) does not reproduce through the normal decide flow, and that
    guard predates this ticket. But `build_report_pdf` decided whether to
    render a pending row purely from `is_draft`, with no defense if a
    decided report's `results` ever contained a pending row anyway (stale
    data from before that guard existed, or a future regression in it) --
    it would silently vanish from BOTH the pending section (`is_draft`
    false) and the Criteria Results table (`shown_ids` excludes pending
    rows unconditionally), while the on-screen `EscalatedPanel.tsx` still
    shows it. This seeds exactly that state directly at the ORM level
    (bypassing `decide_report`, the only way it could occur) and proves
    the pending criterion is still disclosed in the PDF handed to the
    panel."""
    import asyncio

    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.enums import CheckKind, CheckRunStatus, ReportDecision, ResultOutcome
    from app.models.instructor import Instructor
    from app.models.manuscript import Manuscript
    from app.models.rubric import Criterion, Rubric
    from app.models.run import CheckResult, CheckRun, ReadinessReport
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
                    email="prof2@tip.edu.ph",
                    display_name="Prof",
                    password_hash=hash_password("s3cret!"),
                )
                session.add(instructor)
                await session.commit()
                manuscript = Manuscript(
                    instructor_id=instructor.id, group_label="G-12", file_ref="x.pdf"
                )
                rubric = Rubric(
                    instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=True
                )
                session.add_all([manuscript, rubric])
                await session.commit()
                decided_criterion = Criterion(
                    rubric_id=rubric.id,
                    type="structural",
                    text="Has an abstract",
                    evidence=None,
                    weight=Decimal("10"),
                    position=0,
                )
                stuck_criterion = Criterion(
                    rubric_id=rubric.id,
                    type="semantic",
                    text="Chapter 1 states the research problem clearly enough to defend",
                    evidence=None,
                    weight=Decimal("10"),
                    position=1,
                )
                session.add_all([decided_criterion, stuck_criterion])
                await session.commit()
                check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
                session.add(check_run)
                await session.commit()
                session.add(
                    CheckResult(
                        check_run_id=check_run.id,
                        criterion_id=decided_criterion.id,
                        kind=CheckKind.structural,
                        outcome=ResultOutcome.passed,
                        score=100.0,
                        detail={"rule_id": "required_section_present", "anchor": "page 2"},
                    )
                )
                session.add(
                    CheckResult(
                        check_run_id=check_run.id,
                        criterion_id=stuck_criterion.id,
                        kind=CheckKind.semantic,
                        outcome=ResultOutcome.escalated,
                        detail={"reason": "Split vote"},
                    )
                )
                check_run.status = CheckRunStatus.done
                await session.commit()
                await aggregate_and_score(session, check_run.id)
                # Decided directly at the ORM level -- `decide_report`
                # itself would refuse this (proven by the test named
                # above); this simulates the only way a decided report
                # could ever end up with a still-pending row.
                report = await session.scalar(
                    select(ReadinessReport).where(ReadinessReport.check_run_id == check_run.id)
                )
                report.decision = ReportDecision.approved
                await session.commit()
                return check_run.id
        finally:
            await engine.dispose()

    check_run_id = asyncio.run(seed())
    client.post("/auth/login", json={"email": "prof2@tip.edu.ph", "password": "s3cret!"})

    resp = client.get(f"/check-runs/{check_run_id}/report/export.pdf")
    assert resp.status_code == 200

    import fitz

    doc = fitz.open(stream=resp.content, filetype="pdf")
    full_text = "\n".join(page.get_text() for page in doc)
    # The stuck criterion must appear SOMEWHERE in the decided PDF --
    # pre-fix, it appeared nowhere at all.
    assert "Chapter 1 states the research problem clearly enough to defend" in full_text
    # A decided report is not a draft -- must not claim to be one.
    assert "Needs Your Review" not in full_text
    assert "Unresolved At Time Of Decision" in full_text
    assert "AI reached no verdict for this criterion." in full_text


def test_export_captions_a_criterion_by_kind_not_by_declared_type(client, api_scratch_url):
    """BUG-082: `CriterionResultOut` carries both `type` (what the rubric
    DECLARES -- `criterion.type`) and `kind` (how it was ACTUALLY checked
    -- `result.kind`). The frontend's `sourceCaption` (`ResultsTable.tsx`)
    has always read `kind`; this PDF's `_source_caption` used to read
    `type` instead, so the two captioned the SAME criterion two different
    ways the moment they diverged -- exactly what the Tier-0/1 cascade
    does on purpose (a `semantic` criterion decided by a deterministic
    rule). Seeds that exact mismatch: a criterion the rubric calls
    `semantic`, checked with `kind=structural`, and asserts the PDF calls
    it "Rule-checked" -- matching the frontend, not the rubric's own
    declared type (which would have said "AI-graded", the pre-fix
    behavior)."""
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
                    email="prof3@tip.edu.ph",
                    display_name="Prof",
                    password_hash=hash_password("s3cret!"),
                )
                session.add(instructor)
                await session.commit()
                manuscript = Manuscript(
                    instructor_id=instructor.id, group_label="G-13", file_ref="x.pdf"
                )
                rubric = Rubric(
                    instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=True
                )
                session.add_all([manuscript, rubric])
                await session.commit()
                # Rubric DECLARES this semantic -- but it was actually
                # decided by a Tier-0 deterministic rule (kind=structural).
                cascaded_criterion = Criterion(
                    rubric_id=rubric.id,
                    type="semantic",
                    text="Has a references section with at least five sources",
                    evidence=None,
                    weight=Decimal("10"),
                    position=0,
                )
                session.add(cascaded_criterion)
                await session.commit()
                check_run = CheckRun(
                    manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
                )
                session.add(check_run)
                await session.commit()
                session.add(
                    CheckResult(
                        check_run_id=check_run.id,
                        criterion_id=cascaded_criterion.id,
                        kind=CheckKind.structural,
                        outcome=ResultOutcome.passed,
                        score=100.0,
                        detail={"rule_id": "required_section_present", "anchor": "page 4"},
                    )
                )
                await session.commit()
                await aggregate_and_score(session, check_run.id)
                return check_run.id
        finally:
            await engine.dispose()

    check_run_id = asyncio.run(seed())
    client.post("/auth/login", json={"email": "prof3@tip.edu.ph", "password": "s3cret!"})

    resp = client.get(f"/check-runs/{check_run_id}/report/export.pdf")
    assert resp.status_code == 200

    import fitz

    doc = fitz.open(stream=resp.content, filetype="pdf")
    full_text = "\n".join(page.get_text() for page in doc)
    # Matches the frontend's `kind`-based caption -- not "AI-graded",
    # which is what `type`-based captioning (the pre-fix behavior) would
    # have printed for a criterion the rubric calls semantic.
    assert "Rule-checked" in full_text
    assert "AI-graded" not in full_text


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
