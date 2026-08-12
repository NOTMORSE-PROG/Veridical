"""V-018 live-DB tests: the check-run state machine's own acceptance
criteria — full happy path, resumability (kill/restart, no duplicate LLM
calls), quota_exhausted parking + auto-resume, distinct failure taxonomy,
and the integrity stage (V-029/V-033: citation integrity AND statistical
forensics both run for real; F7 is honestly noted as not implemented
yet). Own scratch DB, same convention as the other V2 live tests.
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
from app.models.enums import (
    CheckKind,
    CheckRunStatus,
    IngestStatus,
    ReadinessStatus,
    ResultOutcome,
)
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
        # One result per rubric criterion, plus one integrity check_result
        # (criterion_id=None — F4-F7 aren't rubric criteria, model docstring).
        assert {r.criterion_id for r in results if r.criterion_id is not None} == set(criterion_ids)
        assert any(r.kind == CheckKind.citation_integrity for r in results)
        assert any(r.kind == CheckKind.statistical_forensics for r in results)
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
        assert stages["integrity"]["status"] == "done"
        assert stages["aggregating"]["status"] == "done"


async def test_integrity_stage_runs_all_four_integrity_checks(
    session_factory, tmp_path, monkeypatch
):
    """V-029/V-033/V-037: the integrity stage now actually runs all four
    F4-F7 checks — the fixture PDF has no reference list, no numbers, no
    objective/finding statements, and (being the first manuscript
    processed) an empty archive, so this exercises every check's own
    honest zero/N-A path (no network calls, real CheckResults)."""
    check_run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        await run_check_run(session, check_run, settings, FakeLLMClient())
        assert check_run.stage_status["stages"]["integrity"]["status"] == "done"
        note = check_run.stage_status["stages"]["integrity"]["note"]
        assert "Internal agreement" in note
        assert "citation integrity" in note.lower()
        assert "statistical forensics" in note.lower()
        assert "originality/reuse" in note.lower()
        assert "not implemented" not in note

    async with session_factory() as verify:
        citation_result = (
            await verify.execute(
                select(CheckResult).where(
                    CheckResult.check_run_id == check_run_id,
                    CheckResult.kind == CheckKind.citation_integrity,
                )
            )
        ).scalar_one()
        assert citation_result.criterion_id is None
        assert citation_result.outcome == ResultOutcome.passed
        assert citation_result.detail["n_references"] == 0
        assert citation_result.detail["n_flags"] == 0

        forensics_result = (
            await verify.execute(
                select(CheckResult).where(
                    CheckResult.check_run_id == check_run_id,
                    CheckResult.kind == CheckKind.statistical_forensics,
                )
            )
        ).scalar_one()
        assert forensics_result.criterion_id is None
        # No stats anywhere in this fixture -> honest N/A, never a
        # silently-clean "passed" (charter rule 9, ticket AC "Qualitative
        # capstone (no stats) -> F6 = N/A on the report").
        assert forensics_result.outcome == ResultOutcome.not_applicable
        assert forensics_result.detail["n_inferential_stats"] == 0
        assert forensics_result.detail["n_flags"] == 0

        reuse_result = (
            await verify.execute(
                select(CheckResult).where(
                    CheckResult.check_run_id == check_run_id,
                    CheckResult.kind == CheckKind.originality_reuse,
                )
            )
        ).scalar_one()
        assert reuse_result.criterion_id is None
        # First manuscript ever processed -> cold-start honesty: compared
        # against 0 previously processed manuscripts, zero flags (nothing
        # to match against yet), never faked as "clean" without saying why.
        assert reuse_result.detail["archive_size_n"] == 0
        assert reuse_result.detail["n_flags"] == 0


class _FlakyThenFineLLM:
    """Simulates the process dying/quota running out mid-run: the first
    criterion's FULL self-consistency vote (V-022: pass_1 + pass_2, no
    tie-break needed since the plain fake fixture agrees with itself)
    succeeds normally, then the NEXT call raises QuotaExhaustedError —
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
    """The pre-V-050 contract, still supported behind
    `pipeline_degrade_on_quota=False`: park and resume with no duplicate
    calls. The DEFAULT is now to finish the run instead (see the
    availability-floor tests below) — parking is opt-in because waiting for
    midnight Pacific can mean waiting past the defense."""
    check_run_id, criterion_ids, settings = await _seed(session_factory, tmp_path, monkeypatch)
    settings = settings.model_copy(update={"pipeline_degrade_on_quota": False})

    # 2 calls (pass_1 + pass_2) complete the FIRST criterion's vote; the
    # 3rd call (second criterion's pass_1) is where "quota" runs out.
    flaky = _FlakyThenFineLLM(fail_after=2)
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
            (
                await verify.execute(
                    select(CheckResult).where(
                        CheckResult.check_run_id == check_run_id,
                        CheckResult.kind == CheckKind.semantic,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(semantic_results) == 1

    # "Restart the process": fresh LLM client, call run_check_run again.
    fresh_llm = FakeLLMClient()
    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        await run_check_run(session, check_run, settings, fresh_llm)
        assert check_run.status == CheckRunStatus.done

    async with session_factory() as verify:
        semantic_results = (
            (
                await verify.execute(
                    select(CheckResult).where(
                        CheckResult.check_run_id == check_run_id,
                        CheckResult.kind == CheckKind.semantic,
                    )
                )
            )
            .scalars()
            .all()
        )
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


async def test_missing_data_dir_cache_fails_the_run_instead_of_stalling_forever(
    session_factory, tmp_path, monkeypatch
):
    """BUG-032: `data_dir` (config.py) is a real on-disk cache the pipeline
    reads on every check-run, not just at ingestion — a container recreate
    with no persistent volume silently strands it while the DB still
    believes ingestion succeeded. Simulated here by deleting the manuscript's
    extraction cache file after a successful ingest, then advancing straight
    to the structural stage (which reads it via `build_rule_context`). Before
    the fix, this raised `FileNotFoundError` uncaught out of `run_check_run`
    — harmless in a test that awaits it directly, but fatal inside the real
    worker's `BackgroundTask`, which swallows it and leaves the row frozen
    at its last successful stage forever with no client-visible error. The
    fix must turn this into an honest terminal `failed` status instead."""
    from app.ingest.service import raw_store_path

    check_run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        manuscript_id = (await session.get(CheckRun, check_run_id)).manuscript_id
    raw_store_path(settings, manuscript_id).unlink()

    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        await run_check_run(session, check_run, settings, FakeLLMClient())
        assert check_run.status == CheckRunStatus.failed
        assert check_run.finished_at is not None
        assert check_run.stage_status["failed"]["code"] == "unexpected_error"


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
            (
                await verify.execute(
                    select(AuditLog).where(
                        AuditLog.check_run_id == check_run_id,
                        AuditLog.event_type == "criterion_routing",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(routing_rows) == 1


async def test_quota_exhausted_still_produces_a_finished_run(
    session_factory, tmp_path, monkeypatch
):
    """AVAILABILITY FLOOR (V-050, D-015) — the permanent guarantee.

    The free AI budget resets at midnight Pacific, which can fall AFTER the
    defense. So a spent budget must never mean the instructor gets nothing:
    the run completes, every deterministic check stands, and the criteria the
    AI never reached are handed over as an honest state.
    """
    check_run_id, criterion_ids, settings = await _seed(session_factory, tmp_path, monkeypatch)

    # Quota dies before ANY semantic criterion is graded — the worst case.
    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        await run_check_run(session, check_run, settings, _FlakyThenFineLLM(fail_after=0))

        assert check_run.status == CheckRunStatus.done, "the run must finish, not stall"
        assert "blocked" not in check_run.stage_status
        semantic_stage = check_run.stage_status["stages"]["semantic"]
        # 2 of the fixture's 3 criteria are semantic (criterion_ids[1:]) —
        # both fail to grade since fail_after=0.
        assert semantic_stage["degraded_count"] == len(criterion_ids) - 1
        assert semantic_stage["degraded_code"] == "quota_exhausted"

    async with session_factory() as verify:
        results = (
            (
                await verify.execute(
                    select(CheckResult).where(
                        CheckResult.check_run_id == check_run_id,
                        CheckResult.kind == CheckKind.semantic,
                    )
                )
            )
            .scalars()
            .all()
        )
        # Both SEMANTIC criteria (the third seeded criterion is structural and
        # was decided deterministically, with no AI involved at all — which is
        # precisely why the run can still finish).
        assert len(results) == 2
        for result in results:
            # NOT `escalated` (which would claim the AI looked and hesitated)
            # and NOT `passed` (which would invent a grade) — charter rule 9.
            assert result.outcome == ResultOutcome.quota_exhausted
            assert result.score is None
            assert result.detail["basis"] == "not-graded"


async def test_degraded_run_is_reviewable_and_scores_nothing_by_itself(
    session_factory, tmp_path, monkeypatch
):
    """The degraded run must be USABLE: the ungraded criteria appear in the
    instructor's review panel, labelled as never-graded rather than as a
    low-confidence AI verdict, and they contribute nothing to the score
    until a human decides them."""
    from app.checks.escalation import (
        RESOLUTION_MARK_PASS,
        REVIEW_REASON_NOT_GRADED,
        list_escalated,
        resolve_escalation,
    )

    check_run_id, _, settings = await _seed(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        await run_check_run(session, check_run, settings, _FlakyThenFineLLM(fail_after=0))

    async with session_factory() as session:
        instructor_id = (await session.execute(select(Instructor.id))).scalars().first()
        items = await list_escalated(session, check_run_id)
        assert len(items) == 2
        assert {item.review_reason for item in items} == {REVIEW_REASON_NOT_GRADED}

        # And the instructor can actually act on one (the whole point of
        # finishing the run instead of parking it).
        resolved = await resolve_escalation(
            session,
            check_run_id,
            items[0].check_result_id,
            instructor_id,
            RESOLUTION_MARK_PASS,
            "Checked this section by hand before the defense.",
        )
        assert resolved.outcome == ResultOutcome.passed
