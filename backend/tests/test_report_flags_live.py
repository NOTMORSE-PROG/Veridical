"""BUG-033 live-DB tests: `list_flags_for_run` (the `GET
/check-runs/{id}/flags` data source, the report's own flags panel) —
ownership, fixed F4->F7 group ordering, unresolved-before-overridden
ordering, severity ordering within a group, and criterion_text
resolution for flags tied to a rubric criterion.
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.errors import NotFoundError
from app.models.enums import CheckKind, CheckRunStatus, FlagSeverity, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, Flag
from app.report.service import list_flags_for_run

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_reportflagstest"


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
                "TRUNCATE flag, readiness_report, check_result, check_run, criterion, "
                "rubric, manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _seed_run(session_factory) -> tuple[int, int, int]:
    """Returns (instructor_id, check_run_id, criterion_id)."""
    async with session_factory() as session:
        instructor = Instructor(email="report-flags@demo.local", display_name="Flags Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G-9", file_ref="x.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()
        criterion = Criterion(
            rubric_id=rubric.id,
            type="semantic",
            text="The argument is well developed",
            evidence=None,
            weight=Decimal("100"),
            position=0,
        )
        session.add(criterion)
        await session.commit()
        await session.refresh(criterion)

        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        check_run.status = CheckRunStatus.done
        await session.commit()
        await session.refresh(check_run)
        return instructor.id, check_run.id, criterion.id


async def _add_flag(
    session_factory,
    check_run_id: int,
    *,
    kind: CheckKind,
    severity: FlagSeverity,
    overridden: bool = False,
    criterion_id: int | None = None,
) -> int:
    async with session_factory() as session:
        result = CheckResult(
            check_run_id=check_run_id,
            criterion_id=criterion_id,
            kind=kind,
            outcome=ResultOutcome.failed if criterion_id else ResultOutcome.escalated,
            score=None,
            detail={},
        )
        session.add(result)
        await session.commit()
        await session.refresh(result)

        flag = Flag(
            check_result_id=result.id,
            severity=severity,
            evidence_excerpt="Some manuscript text quoted verbatim.",
            page_anchor="page 4",
            overridden=overridden,
            override_reason="Checked by hand." if overridden else None,
        )
        session.add(flag)
        await session.commit()
        await session.refresh(flag)
        return flag.id


async def test_no_flags_returns_an_empty_list(session_factory):
    instructor_id, check_run_id, _ = await _seed_run(session_factory)
    async with session_factory() as session:
        flags = await list_flags_for_run(session, check_run_id, instructor_id)
    assert flags == []


async def test_ownership_check_matches_get_report(session_factory):
    instructor_id, check_run_id, _ = await _seed_run(session_factory)
    await _add_flag(
        session_factory, check_run_id, kind=CheckKind.internal_agreement, severity=FlagSeverity.high
    )
    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await list_flags_for_run(session, check_run_id, instructor_id + 999)


async def test_flags_ordered_by_fixed_check_kind_then_unresolved_first_then_severity(
    session_factory,
):
    """BUG-033/`ui-designer` spec: F4->F7 declaration order is fixed
    (never severity-sorted at the group level — recognition over
    recall), unresolved flags sort before already-overridden ones within
    a group, and severity (high, med, low) breaks remaining ties."""
    instructor_id, check_run_id, _ = await _seed_run(session_factory)

    # Seeded deliberately out of order to prove sorting, not insertion
    # order, drives the result.
    reuse_id = await _add_flag(
        session_factory, check_run_id, kind=CheckKind.originality_reuse, severity=FlagSeverity.low
    )
    citation_overridden_id = await _add_flag(
        session_factory,
        check_run_id,
        kind=CheckKind.citation_integrity,
        severity=FlagSeverity.high,
        overridden=True,
    )
    citation_unresolved_low_id = await _add_flag(
        session_factory, check_run_id, kind=CheckKind.citation_integrity, severity=FlagSeverity.low
    )
    citation_unresolved_high_id = await _add_flag(
        session_factory, check_run_id, kind=CheckKind.citation_integrity, severity=FlagSeverity.high
    )
    agreement_id = await _add_flag(
        session_factory, check_run_id, kind=CheckKind.internal_agreement, severity=FlagSeverity.med
    )
    forensics_id = await _add_flag(
        session_factory,
        check_run_id,
        kind=CheckKind.statistical_forensics,
        severity=FlagSeverity.med,
    )

    async with session_factory() as session:
        flags = await list_flags_for_run(session, check_run_id, instructor_id)

    assert [f.id for f in flags] == [
        agreement_id,  # internal_agreement (F4) first
        citation_unresolved_high_id,  # citation_integrity (F5): unresolved before overridden,
        citation_unresolved_low_id,  # high before low within unresolved,
        citation_overridden_id,  # overridden last within this group
        forensics_id,  # statistical_forensics (F6)
        reuse_id,  # originality_reuse (F7) last
    ]


async def test_criterion_text_resolved_when_flag_is_tied_to_a_criterion(session_factory):
    """F4-F7 flags are usually criterion_id=None (not tied to a rubric
    criterion, ENGINEERING §2), but the field must resolve correctly on
    the rare row that does carry one, mirroring `flags/service.py`'s own
    `_to_flag_out` behavior."""
    instructor_id, check_run_id, criterion_id = await _seed_run(session_factory)
    flag_id = await _add_flag(
        session_factory,
        check_run_id,
        kind=CheckKind.internal_agreement,
        severity=FlagSeverity.med,
        criterion_id=criterion_id,
    )
    async with session_factory() as session:
        flags = await list_flags_for_run(session, check_run_id, instructor_id)
    assert len(flags) == 1
    assert flags[0].id == flag_id
    assert flags[0].criterion_text == "The argument is well developed"


async def test_flag_without_a_criterion_has_null_criterion_text(session_factory):
    instructor_id, check_run_id, _ = await _seed_run(session_factory)
    await _add_flag(
        session_factory, check_run_id, kind=CheckKind.citation_integrity, severity=FlagSeverity.low
    )
    async with session_factory() as session:
        flags = await list_flags_for_run(session, check_run_id, instructor_id)
    assert flags[0].criterion_text is None
