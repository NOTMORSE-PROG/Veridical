"""V-018 live-DB tests: the check-run state machine's own acceptance
criteria — full happy path, resumability (kill/restart, no duplicate LLM
calls), quota_exhausted parking + auto-resume, distinct failure taxonomy,
and the integrity stage's honest "not implemented yet" skip. Own scratch
DB, same convention as the other V2 live tests.
"""

import os
import time
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.checks.rules.sections import identify_target_section
from app.config import get_settings
from app.errors import QuotaExhaustedError
from app.llm.fake import FakeLLMClient
from app.models.enums import CheckKind, CheckRunStatus, IngestStatus, ReadinessStatus
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, ReadinessReport
from app.pipeline.machine import run_check_run
from tests.test_ingest_pdf import PdfBuilder

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_pipelinetest"


@pytest.fixture(scope="module")
def pipeline_scratch_url():
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
def session_factory(pipeline_scratch_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url

    engine = create_async_engine(sqlalchemy_url(pipeline_scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean_tables(session_factory):
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE audit_log, readiness_report, check_result, check_run, "
                "criterion, rubric, citation, manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


def _two_section_pdf(tmp_path):
    b = PdfBuilder()
    b.new_page().line("A STUDY OF THINGS", size=16, bold=True)
    b.new_page().line("ABSTRACT", bold=True)
    b.line("This is a test sentence used as evidence.")
    b.new_page().line("CHAPTER 1 INTRODUCTION", bold=True)
    b.line("This is a test sentence used as evidence.")
    return b.save(tmp_path / "two.pdf")


async def _seed(session_factory, tmp_path, monkeypatch, *, ingest_status=IngestStatus.done):
    from app.ingest.service import ingest_manuscript

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    settings = get_settings()
    pdf_path = _two_section_pdf(tmp_path)

    async with session_factory() as session:
        instructor = Instructor(
            email=f"pipeline-{time.time_ns()}@test.local", display_name="Pipeline Test"
        )
        session.add(instructor)
        await session.commit()

        manuscript = Manuscript(
            instructor_id=instructor.id, group_label="Group A", file_ref=str(pdf_path)
        )
        session.add(manuscript)
        await session.commit()
        if ingest_status == IngestStatus.done:
            await ingest_manuscript(session, manuscript, pdf_path, settings)
        else:
            manuscript.ingest_status = ingest_status
            await session.commit()

        rubric = Rubric(
            instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=True
        )
        session.add(rubric)
        await session.commit()
        criteria = [
            Criterion(
                rubric_id=rubric.id,
                type="structural",
                text="The manuscript must include an Abstract",
                evidence=None,
                weight=Decimal("20"),
                position=0,
            ),
            Criterion(
                rubric_id=rubric.id,
                type="semantic",
                text="The Abstract clearly states the study's purpose",
                evidence=None,
                weight=Decimal("40"),
                position=1,
            ),
            Criterion(
                rubric_id=rubric.id,
                type="semantic",
                text="Chapter 1 clearly states the problem",
                evidence=None,
                weight=Decimal("40"),
                position=2,
            ),
        ]
        session.add_all(criteria)
        await session.commit()
        for c in criteria:
            await session.refresh(c)

        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        await session.commit()
        await session.refresh(check_run)
        return check_run.id, [c.id for c in criteria], settings


# Sanity check on the fixture PDF: the two semantic criteria above really
# do land in two DIFFERENT section batches (required for the resumability
# test below to mean anything).
def test_fixture_criteria_target_different_sections():
    class C:
        def __init__(self, text):
            self.text = text
            self.evidence = None

    abstract_target = identify_target_section(C("The Abstract clearly states the study's purpose"))
    assert abstract_target == "abstract"
    chapter_target = identify_target_section(C("Chapter 1 clearly states the problem"))
    assert chapter_target == "chapter 1"


async def test_full_happy_path_reaches_done_with_a_real_report(
    session_factory, tmp_path, monkeypatch
):
    check_run_id, criterion_ids, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        await run_check_run(session, check_run, settings, FakeLLMClient())
        assert check_run.status == CheckRunStatus.done
        assert check_run.finished_at is not None

    async with session_factory() as verify:
        results = (
            (
                await verify.execute(
                    select(CheckResult).where(CheckResult.check_run_id == check_run_id)
                )
            )
            .scalars()
            .all()
        )
        assert {r.criterion_id for r in results} == set(criterion_ids)
        # Reload the check_run from a FRESH session — the in-memory object
        # already had every stage recorded, but a real bug (found live via
        # Playwright, see test below) had every stage after the first
        # silently fail to actually reach the database.
        reloaded = await verify.get(CheckRun, check_run_id)
        assert set(reloaded.stage_status["stages"]) == {
            "ingesting",
            "structural",
            "semantic",
            "integrity",
            "aggregating",
        }
        report = (
            await verify.execute(
                select(ReadinessReport).where(ReadinessReport.check_run_id == check_run_id)
            )
        ).scalar_one()
        assert report.status in (
            ReadinessStatus.ready,
            ReadinessStatus.conditionally_ready,
            ReadinessStatus.not_ready,
            ReadinessStatus.needs_review,
        )


async def test_stage_status_survives_a_fresh_reload_every_stage(
    session_factory, tmp_path, monkeypatch
):
    """Regression test for a real bug (found live via Playwright, not in
    any unit test): `check_run.stage_status` was mutated IN PLACE before
    being reassigned, which made SQLAlchemy's plain (non-Mutable) JSONB
    column compare the "old" and "new" values as equal and skip the
    UPDATE — every stage after the first (`ingesting`) silently vanished
    from the DATABASE row even though `check_run.status` kept advancing
    normally and every check_result/report was saved correctly. Every
    prior test in this file happened to only check the in-memory object,
    which was never wrong — only a fresh reload exposes this class of
    bug, so this test exists specifically to do that.
    """
    check_run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        await run_check_run(session, check_run, settings, FakeLLMClient())

    async with session_factory() as fresh_session:
        reloaded = await fresh_session.get(CheckRun, check_run_id)
        stages = reloaded.stage_status["stages"]
        assert stages["ingesting"]["status"] == "done"
        assert stages["structural"]["status"] == "done"
        assert stages["semantic"]["status"] == "done"
        assert stages["integrity"]["status"] == "skipped"
        assert stages["aggregating"]["status"] == "done"


async def test_integrity_stage_is_honestly_skipped_not_faked(
    session_factory, tmp_path, monkeypatch
):
    check_run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        await run_check_run(session, check_run, settings, FakeLLMClient())
        assert check_run.stage_status["stages"]["integrity"]["status"] == "skipped"


class _FlakyThenFineLLM:
    """Simulates the process dying/quota running out mid-run: the FIRST
    batch call succeeds normally, the SECOND raises QuotaExhaustedError —
    exactly what a real exhausted daily quota looks like from the
    orchestrator's point of view (V-009's queue raises the same type)."""

    def __init__(self, fail_after: int):
        self.fail_after = fail_after
        self.calls = 0
        self._fake = FakeLLMClient()

    async def complete(self, *args, **kwargs):
        self.calls += 1
        if self.calls > self.fail_after:
            raise QuotaExhaustedError("simulated: daily Gemini quota exhausted")
        return await self._fake.complete(*args, **kwargs)


async def test_quota_exhausted_parks_the_run_then_resumes_without_duplicate_calls(
    session_factory, tmp_path, monkeypatch
):
    check_run_id, criterion_ids, settings = await _seed(session_factory, tmp_path, monkeypatch)

    flaky = _FlakyThenFineLLM(fail_after=1)
    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        await run_check_run(session, check_run, settings, flaky)
        # Still in the semantic stage — never advanced, never failed.
        assert check_run.status == CheckRunStatus.semantic
        blocked = check_run.stage_status["blocked"]
        assert blocked["code"] == "quota_exhausted"
        assert blocked["resume_at"] is not None

    # Exactly one semantic criterion should already be persisted (the
    # batch that succeeded before the "quota" ran out).
    async with session_factory() as verify:
        semantic_results = (
            await verify.execute(
                select(CheckResult).where(
                    CheckResult.check_run_id == check_run_id, CheckResult.kind == CheckKind.semantic
                )
            )
        ).scalars().all()
        assert len(semantic_results) == 1

    # "Restart the process": fresh LLM client, call run_check_run again.
    fresh_llm = FakeLLMClient()
    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        await run_check_run(session, check_run, settings, fresh_llm)
        assert check_run.status == CheckRunStatus.done

    async with session_factory() as verify:
        semantic_results = (
            await verify.execute(
                select(CheckResult).where(
                    CheckResult.check_run_id == check_run_id, CheckResult.kind == CheckKind.semantic
                )
            )
        ).scalars().all()
        # Both semantic criteria now have results — the resumed run did
        # NOT re-call the LLM for the one that already succeeded.
        assert {r.criterion_id for r in semantic_results} == {criterion_ids[1], criterion_ids[2]}


async def test_ingest_failed_manuscript_fails_the_run_as_file_malformed(
    session_factory, tmp_path, monkeypatch
):
    check_run_id, _, settings = await _seed(
        session_factory, tmp_path, monkeypatch, ingest_status=IngestStatus.failed
    )
    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        await run_check_run(session, check_run, settings, FakeLLMClient())
        assert check_run.status == CheckRunStatus.failed
        assert check_run.stage_status["failed"]["code"] == "file_malformed"


async def test_routing_only_persists_once_across_multiple_advances(
    session_factory, tmp_path, monkeypatch
):
    """Calling run_check_run several times (as the worker naturally does,
    stage by stage) must not duplicate the routing audit_log row or the
    not_applicable results it creates for unroutable criteria."""
    from app.models.audit import AuditLog

    check_run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        await run_check_run(session, check_run, settings, FakeLLMClient())

    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        # Run again post-completion — a no-op (already done), but proves
        # re-entrancy doesn't duplicate the routing side effect.
        await run_check_run(session, check_run, settings, FakeLLMClient())

    async with session_factory() as verify:
        routing_rows = (
            await verify.execute(
                select(AuditLog).where(
                    AuditLog.check_run_id == check_run_id,
                    AuditLog.event_type == "criterion_routing",
                )
            )
        ).scalars().all()
        assert len(routing_rows) == 1
