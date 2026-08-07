"""V-026 live-DB tests: flag detail, annotation, and override — the
override recomputes score/status live (ticket AC), the original AI
finding is never destroyed, and cross-instructor access is rejected.

`_seed_flagged_run` below seeds a Flag directly (no `flag.detail`,
reason lives on `check_result.detail`) rather than running a real check —
this still matches the ORIGINAL one-flag-per-check_result shape V-020's
semantic grading produces, so it doubles as regression coverage for
`_to_flag_out`'s fallback branch. F5/F6 (V-027-033, V4) now produce real
flags with their OWN per-flag `detail` (many flags share one
check_result) — see `test_flag_detail_takes_priority_over_check_result_detail`
below for that shape.
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.errors import NotFoundError
from app.flags.service import annotate_flag, get_flag, override_flag
from app.models.enums import CheckKind, CheckRunStatus, FlagSeverity, ReadinessStatus, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, Flag
from app.report.service import aggregate_and_score

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_flagstest"


@pytest.fixture(scope="module")
def scratch_url():
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
def session_factory(scratch_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url

    engine = create_async_engine(sqlalchemy_url(scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean_tables(session_factory):
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE audit_log, flag, readiness_report, check_result, check_run, "
                "criterion, rubric, manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _seed_flagged_run(session):
    """A `citation_integrity` check_result (F5, not a rubric criterion —
    criterion_id NULL, same shape a real V4 check will eventually
    produce) carrying one HIGH-severity flag, plus one PASSING semantic
    criterion so the composite score is a real number a flag deduction
    can visibly move."""
    instructor = Instructor(email=f"flags-{id(session)}@test.local", display_name="Flags Test")
    session.add(instructor)
    await session.commit()
    manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
    rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
    session.add_all([manuscript, rubric])
    await session.commit()
    criterion = Criterion(
        rubric_id=rubric.id,
        type="semantic",
        text="Chapter 1 clearly states the problem",
        evidence=None,
        weight=Decimal("100"),
        position=0,
    )
    session.add(criterion)
    await session.commit()
    check_run = CheckRun(
        manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
    )
    session.add(check_run)
    await session.commit()

    passing_result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=criterion.id,
        kind=CheckKind.semantic,
        outcome=ResultOutcome.passed,
        score=100.0,
        detail={"basis": "llm", "verdict": "pass", "reasoning": "Clearly stated."},
    )
    flagged_result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=None,
        kind=CheckKind.citation_integrity,
        outcome=ResultOutcome.failed,
        score=None,
        detail={
            "basis": "external-api",
            "verdict": "not_supported",
            "reasoning": "Retracted source.",
        },
    )
    session.add_all([passing_result, flagged_result])
    await session.commit()
    flag = Flag(
        check_result_id=flagged_result.id,
        severity=FlagSeverity.high,
        confidence=Decimal("1.000"),
        evidence_excerpt="Wang, S. (2019). A study of things.",
        page_anchor="page 34",
    )
    session.add(flag)
    await session.commit()
    await aggregate_and_score(session, check_run.id)
    return instructor, check_run, flag


async def test_get_flag_shows_ai_finding_and_evidence(session_factory):
    async with session_factory() as session:
        instructor, _, flag = await _seed_flagged_run(session)
        out = await get_flag(session, flag.id, instructor.id)
        assert out.severity == "high"
        assert out.confidence == 1.0
        assert out.evidence_excerpt == "Wang, S. (2019). A study of things."
        assert out.ai_verdict_summary == "not_supported"
        assert out.overridden is False


async def test_annotate_saves_free_text(session_factory):
    async with session_factory() as session:
        instructor, _, flag = await _seed_flagged_run(session)
        out = await annotate_flag(session, flag.id, instructor.id, "Confirmed with the adviser.")
        assert out.annotation == "Confirmed with the adviser."


async def test_override_recomputes_score_live_and_preserves_the_original_finding(session_factory):
    async with session_factory() as session:
        instructor, check_run, flag = await _seed_flagged_run(session)

        before = await aggregate_and_score(session, check_run.id)
        # High-severity flag deduction applied — Not Ready despite a
        # perfect 100% weighted criterion score (ENGINEERING §5).
        assert before.status == ReadinessStatus.not_ready

        flag_out, check_run_id = await override_flag(
            session, flag.id, instructor.id, "Verified — this citation is not actually retracted."
        )
        assert check_run_id == check_run.id
        assert flag_out.overridden is True
        assert flag_out.override_reason == "Verified — this citation is not actually retracted."
        # Original AI finding untouched — never destroyed by the override:
        assert flag_out.evidence_excerpt == "Wang, S. (2019). A study of things."
        assert flag_out.ai_verdict_summary == "not_supported"

        after = await aggregate_and_score(session, check_run.id)
        assert after.status == ReadinessStatus.ready  # deduction gone, high-flag gate cleared
        assert after.composite_score == Decimal("100.00")


async def _seed_two_flags_one_check_result(session):
    """Mirrors the REAL F5/F6 shape (V-027-033): many `Flag` rows under ONE
    `check_result`, each with its own `detail` — the shape
    `check_result.detail`'s single top-level key can't represent (V-033's
    reason for adding `Flag.detail`). Proves two flags sharing a
    check_result get independent reasons rather than the check_result's
    one shared `detail` bleeding into both."""
    instructor = Instructor(email=f"flags3-{id(session)}@test.local", display_name="Flags Test 3")
    session.add(instructor)
    await session.commit()
    manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.docx")
    rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
    session.add_all([manuscript, rubric])
    await session.commit()
    check_run = CheckRun(
        manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
    )
    session.add(check_run)
    await session.commit()

    # check_result.detail carries only aggregate counts (V-033's real
    # shape) — never a per-flag reason — so a stale fallback to it would
    # surface the WRONG text for both flags below.
    result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=None,
        kind=CheckKind.statistical_forensics,
        outcome=ResultOutcome.passed,
        detail={"n_inferential_stats": 1, "n_descriptive_stats": 1, "n_flags": 2},
    )
    session.add(result)
    await session.commit()
    grim_flag = Flag(
        check_result_id=result.id,
        severity=FlagSeverity.high,
        evidence_excerpt="n=10, M=3.33",
        page_anchor="page 12",
        detail={"kind": "grim_inconsistent", "reason": "Reported mean is not a possible value."},
    )
    pvalue_flag = Flag(
        check_result_id=result.id,
        severity=FlagSeverity.med,
        evidence_excerpt="t(28)=2.45, p=.500",
        page_anchor="page 14",
        detail={"kind": "p_value_mismatch", "reason": "Recomputed p-value does not match."},
    )
    session.add_all([grim_flag, pvalue_flag])
    await session.commit()
    return instructor, grim_flag, pvalue_flag


async def test_flag_detail_takes_priority_over_check_result_detail(session_factory):
    async with session_factory() as session:
        instructor, grim_flag, pvalue_flag = await _seed_two_flags_one_check_result(session)

        grim_out = await get_flag(session, grim_flag.id, instructor.id)
        assert grim_out.ai_verdict_summary is None  # no "verdict"/"basis" key on this detail shape
        assert grim_out.ai_reasoning == "Reported mean is not a possible value."

        pvalue_out = await get_flag(session, pvalue_flag.id, instructor.id)
        assert pvalue_out.ai_reasoning == "Recomputed p-value does not match."

        # Two flags, one check_result, two DISTINCT reasons — proves
        # `flag.detail` (not `check_result.detail`) is the source.
        assert grim_out.ai_reasoning != pvalue_out.ai_reasoning


async def test_cross_instructor_access_is_rejected_not_leaked(session_factory):
    async with session_factory() as session:
        _, _, flag = await _seed_flagged_run(session)
        stranger = Instructor(email="stranger@test.local", display_name="Stranger")
        session.add(stranger)
        await session.commit()

        with pytest.raises(NotFoundError):
            await get_flag(session, flag.id, stranger.id)
        with pytest.raises(NotFoundError):
            await override_flag(session, flag.id, stranger.id, "reason")
