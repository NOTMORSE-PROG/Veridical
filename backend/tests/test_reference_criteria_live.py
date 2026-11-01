"""V-098 live-DB tests: reference criteria judged from the reference list are
written to real stored rows, with their audit events, exactly once."""

import os
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.checks import reference_criteria as rc
from app.config import get_settings
from app.models.audit import AuditLog
from app.models.citation import Citation
from app.models.enums import CheckKind, CheckRunStatus, CitationParseStatus, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun
from tests.test_reference_criteria import BIBLIOGRAPHY, QUALITY

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_referencecriteriatest"


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
                "TRUNCATE audit_log, citation, check_result, check_run, criterion, rubric, "
                "manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


@pytest.fixture()
def data():
    return rc.load_reference_data(get_settings().reference_criteria_file)


ENTRIES = [
    "Wang, R. (1996). Beyond accuracy. Journal of Management Information Systems, 12(4), 5–33.",
    "Biggs, J. (1996). Enhancing teaching. Higher Education, 32, 347–364.",
    "Lee, K. (2020). A study of apps. Computers in Human Behavior, 3(2), 1–9.",
    "Cruz, A. (2021). Mobile learning. Journal of Educational Technology, 8(1), 11–20.",
    "Reyes, B. (2022). Face shapes. ResearchGate. https://www.researchgate.net/publication/1",
    "Santos, C. (2023). Travel tips. Retrieved May 1, 2023 from https://shop.example.com/tips",
]


async def _seed(session_factory, *, grader_outcome=ResultOutcome.passed, detail_extra=None):
    """A run whose format has the T.I.P. Bibliography and Quality criteria,
    graded EXEMPLARY with a stored (Decimal) score, after a citation check."""
    async with session_factory() as session:
        instructor = Instructor(email="reference-live@demo.local", display_name="Ref Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G-1", file_ref="x.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()
        criteria = [
            Criterion(
                rubric_id=rubric.id,
                type="semantic",
                text=item.text,
                evidence=None,
                weight=Decimal("1"),
                position=index,
                levels=item.levels,
            )
            for index, item in enumerate((BIBLIOGRAPHY, QUALITY))
        ]
        session.add_all(criteria)
        run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        run.status = CheckRunStatus.done
        session.add(run)
        await session.commit()
        for criterion in criteria:
            session.add(
                CheckResult(
                    check_run_id=run.id,
                    criterion_id=criterion.id,
                    kind=CheckKind.semantic,
                    outcome=grader_outcome,
                    score=Decimal("4.00"),
                    detail={
                        "basis": "llm",
                        "verdict": "EXEMPLARY",
                        "level": {"name": "EXEMPLARY"},
                        "reasoning": "grader",
                        "reason_codes": ["grader_code"],
                        **(detail_extra or {}),
                    },
                )
            )
        session.add_all(
            Citation(
                manuscript_id=manuscript.id,
                order_index=index,
                raw_text=raw,
                parse_status=CitationParseStatus.parsed,
            )
            for index, raw in enumerate(ENTRIES)
        )
        citation_result = CheckResult(
            check_run_id=run.id,
            kind=CheckKind.citation_integrity,
            outcome=ResultOutcome.passed,
            detail={
                "reference_membership_status": "complete",
                "reference_metadata_coverage": [
                    {"reference_order_index": index, "status": "not_found"}
                    for index in range(len(ENTRIES))
                ],
            },
        )
        session.add(citation_result)
        await session.commit()
        return run.id, [c.id for c in criteria]


async def _apply(session_factory, run_id, data):
    async with session_factory() as session:
        criteria = list(
            (
                await session.scalars(
                    select(Criterion).join(Rubric).join(CheckRun).where(CheckRun.id == run_id)
                )
            ).all()
        )
        citation_result = await session.scalar(
            select(CheckResult).where(
                CheckResult.check_run_id == run_id,
                CheckResult.kind == CheckKind.citation_integrity,
            )
        )
        citations = list((await session.scalars(select(Citation))).all())
        return await rc.apply_reference_strategies(
            session, run_id, criteria, citation_result, citations, data
        )


async def _rows(session_factory, run_id):
    async with session_factory() as session:
        return {
            row.criterion_id: row
            for row in (
                await session.scalars(
                    select(CheckResult).where(
                        CheckResult.check_run_id == run_id,
                        CheckResult.kind == CheckKind.semantic,
                    )
                )
            ).all()
        }


async def test_both_criteria_are_written_to_the_stored_rows_with_their_audit_events(
    session_factory, data
):
    run_id, (bibliography_id, quality_id) = await _seed(session_factory)
    assert await _apply(session_factory, run_id, data) == 2
    rows = await _rows(session_factory, run_id)
    bibliography = rows[bibliography_id]
    # 4 papers typed from their citation details; a repository copy and an
    # apparent web page could still be major references, so nothing is
    # lowered: the counts fit level 2 at worst and level 4 at best.
    assert bibliography.outcome == ResultOutcome.escalated and bibliography.score is None
    record = bibliography.detail["reference_strategy"]
    assert record["strategy"] == rc.INVENTORY
    assert record["count_level_bounds"] == [2, 4]
    assert record["grader"]["score"] == 4.0 and record["grader"]["reason_codes"] == ["grader_code"]
    assert "reason_codes" not in bibliography.detail
    assert bibliography.detail["proposed_verdict"] == "EXEMPLARY"
    assert (
        "Entries to check, by their place in the reference list: 5 to 6."
        in (bibliography.detail["reason"])
    )
    quality = rows[quality_id]
    assert quality.outcome == ResultOutcome.escalated
    assert quality.detail["reference_strategy"]["strategy"] == rc.QUALITY
    async with session_factory() as session:
        events = (
            await session.scalars(
                select(AuditLog).where(AuditLog.event_type == "reference_criterion_decided")
            )
        ).all()
    assert len(events) == 2


async def test_a_second_pass_changes_nothing(session_factory, data):
    run_id, _ = await _seed(session_factory)
    await _apply(session_factory, run_id, data)
    before = {k: dict(v.detail) for k, v in (await _rows(session_factory, run_id)).items()}
    assert await _apply(session_factory, run_id, data) == 0
    after = {k: dict(v.detail) for k, v in (await _rows(session_factory, run_id)).items()}
    assert before == after


@pytest.mark.parametrize(
    ("outcome", "extra"),
    [
        (ResultOutcome.api_down, None),
        (ResultOutcome.passed, {"coverage_reason": "source_reading_order_partial"}),
        (ResultOutcome.passed, {"injection_suspected": True}),
    ],
)
async def test_rows_the_product_already_could_not_assess_are_left_alone(
    session_factory, data, outcome, extra
):
    run_id, _ = await _seed(session_factory, grader_outcome=outcome, detail_extra=extra)
    assert await _apply(session_factory, run_id, data) == 0
    for row in (await _rows(session_factory, run_id)).values():
        assert "reference_strategy" not in row.detail and row.outcome == outcome


async def test_a_partly_read_list_escalates_without_counting(session_factory, data):
    run_id, (bibliography_id, _) = await _seed(session_factory)
    async with session_factory() as session:
        citation_result = await session.scalar(
            select(CheckResult).where(CheckResult.kind == CheckKind.citation_integrity)
        )
        citation_result.detail = {**citation_result.detail, "unread_pages": [61]}
        await session.commit()
    await _apply(session_factory, run_id, data)
    row = (await _rows(session_factory, run_id))[bibliography_id]
    assert row.outcome == ResultOutcome.escalated
    assert "pages 61" in row.detail["reason"]
    assert "profile" not in row.detail["reference_strategy"]
