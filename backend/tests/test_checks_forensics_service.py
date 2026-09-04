"""V-033 tests: the F6 assembly (`run_statistical_forensics_check`) — the
applicability gate (ticket AC: "Qualitative capstone (no stats) → F6 =
N/A on the report") and real Flag persistence end-to-end. Live Postgres
(own scratch DB, same convention as test_checks_citations_verify.py)."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.checks.forensics.service import run_statistical_forensics_check
from app.db import sqlalchemy_url
from app.ingest.schemas import ExtractionResult, SectionTree, TableBlock, TextBlock
from app.models.enums import ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_forensicstest"


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
    engine = create_async_engine(sqlalchemy_url(scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean(session_factory):
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE audit_log, flag, check_result, check_run, rubric, "
                "manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _seed_check_run(session_factory) -> int:
    async with session_factory() as session:
        instructor = Instructor(email="forensics-test@test.local", display_name="Forensics Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(
            instructor_id=instructor.id, group_label="Group A", file_ref="test.pdf"
        )
        session.add(manuscript)
        await session.commit()
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add(rubric)
        await session.commit()
        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        await session.commit()
        return check_run.id


def _block(text_: str, *, page: int = 1) -> TextBlock:
    return TextBlock(text=text_, page=page, max_font_size=11.0, bold_ratio=0.0)


def _extraction(*, blocks=None, tables=None) -> ExtractionResult:
    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=100,
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=blocks or [],
        images=[],
        tables=tables or [],
    )


async def test_qualitative_manuscript_with_no_stats_is_not_applicable(session_factory):
    """Ticket AC: 'Qualitative capstone (no stats) → F6 = N/A on the
    report' — N/A is not 'passed' (charter rule 9)."""
    check_run_id = await _seed_check_run(session_factory)
    extraction = _extraction(blocks=[_block("This chapter discusses the themes qualitatively.")])
    async with session_factory() as session:
        result = await run_statistical_forensics_check(session, check_run_id, extraction)
    assert result.outcome == ResultOutcome.not_applicable
    assert result.detail["n_inferential_stats"] == 0
    assert result.detail["n_descriptive_stats"] == 0
    assert result.detail["n_flags"] == 0


async def test_manuscript_with_stats_is_passed_even_with_no_flags(session_factory):
    check_run_id = await _seed_check_run(session_factory)
    extraction = _extraction(blocks=[_block("The result was significant, t(28) = 2.45, p = .021.")])
    async with session_factory() as session:
        result = await run_statistical_forensics_check(session, check_run_id, extraction)
    assert result.outcome == ResultOutcome.passed
    assert result.detail["n_inferential_stats"] == 1
    assert result.detail["n_flags"] == 0


async def test_seeded_grim_impossible_mean_produces_a_real_flag_row(session_factory):
    """End-to-end: a descriptive table with a GRIM-impossible mean
    produces a real persisted Flag, not just an in-memory draft."""
    check_run_id = await _seed_check_run(session_factory)
    table = TableBlock(
        page=5,
        rows=[["Group", "n", "M"], ["Control", "10", "3.33"]],
        source="native",
    )
    extraction = _extraction(tables=[table])
    async with session_factory() as session:
        result = await run_statistical_forensics_check(session, check_run_id, extraction)
        assert result.outcome == ResultOutcome.passed
        assert result.detail["n_flags"] == 1

        flag_query = text("SELECT severity, page_anchor FROM flag WHERE check_result_id = :id")
        rows = (await session.execute(flag_query, {"id": result.id})).all()
    assert len(rows) == 1
    assert rows[0].severity == "med"
    assert rows[0].page_anchor == "p. 5"


async def test_bug_151_writes_a_check_computed_audit_event(session_factory):
    """BUG-151: F6 makes zero LLM calls, so before this fix it wrote
    NOTHING to the audit log -- a run's forensics verdict had no record of
    how it was reached (charter judgment 4). Covers the main (has-stats)
    path; the reuse check's own sibling test covers the pattern's early-
    return branch (backend-critic: only one of five new call sites had any
    test coverage before this)."""
    check_run_id = await _seed_check_run(session_factory)
    extraction = _extraction(blocks=[_block("The result was significant, t(28) = 2.45, p = .021.")])
    async with session_factory() as session:
        await run_statistical_forensics_check(session, check_run_id, extraction)

        row = (
            await session.execute(
                text(
                    "SELECT payload FROM audit_log WHERE check_run_id = :id "
                    "AND event_type = 'statistical_forensics_check_computed'"
                ),
                {"id": check_run_id},
            )
        ).first()
    assert row is not None
    assert row.payload["outcome"] == "passed"
    assert row.payload["n_inferential_stats"] == 1


async def test_wrong_p_value_and_grim_impossible_mean_both_flagged_together(session_factory):
    """Text-based (p-recalc) and table-based (GRIM) findings both surface
    from the same check_run — the assembly combines every sub-check."""
    check_run_id = await _seed_check_run(session_factory)
    extraction = _extraction(
        blocks=[_block("The effect was significant, t(28) = 2.45, p = .900.")],
        tables=[
            TableBlock(page=5, rows=[["Group", "n", "M"], ["A", "10", "3.33"]], source="native")
        ],
    )
    async with session_factory() as session:
        result = await run_statistical_forensics_check(session, check_run_id, extraction)
        assert result.detail["n_flags"] == 2
        kind_query = text(
            "SELECT evidence_excerpt FROM flag WHERE check_result_id = :id ORDER BY id"
        )
        rows = (await session.execute(kind_query, {"id": result.id})).scalars().all()
    assert any("2.45" in r for r in rows)  # the p-recalc flag
    assert any("3.33" in r for r in rows)  # the GRIM flag
