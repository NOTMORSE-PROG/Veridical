"""BUG-049 regression: a fake-LLM-mode run's fixture data (a canned vision
table, a fabricated statistical-forensics flag with a real page anchor)
used to render identically to a genuine finding everywhere a verdict is
shown -- no disclosure on the report, the flag evidence page, the exported
PDF, or the public adviser link, and the mode wasn't even persisted, so an
old report couldn't be told apart from a real one after the fact.

Three things must now hold: (1) `create_check_run` persists `llm_mode` at
creation from `settings.veridical_fake_llm`; (2) that mode reaches the
instructor's own report, the flag evidence page, AND the public
(unauthenticated) adviser view; (3) a legacy row backfilled to "unknown"
(migration 0024) still reports itself honestly rather than claiming "real".
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.config import Settings
from app.flags.service import get_flag
from app.models.enums import CheckKind, CheckRunStatus, FlagSeverity, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, Flag
from app.pipeline.service import create_check_run
from app.report.service import aggregate_and_score, get_report
from app.share.service import create_share_link, get_shared_report

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_bug049test"


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
                "TRUNCATE audit_log, share_link, flag, readiness_report, check_result, "
                "check_run, criterion, rubric, manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _seed_instructor_and_pair(session):
    instructor = Instructor(email=f"bug049-{id(session)}@test.local", display_name="BUG-049 Test")
    session.add(instructor)
    await session.commit()
    manuscript = Manuscript(
        instructor_id=instructor.id,
        group_label="G-49",
        file_ref="x.pdf",
        ingest_status="done",
    )
    rubric = Rubric(
        instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=True
    )
    session.add_all([manuscript, rubric])
    await session.commit()
    await session.refresh(manuscript)
    await session.refresh(rubric)
    return instructor, manuscript, rubric


async def test_create_check_run_persists_fake_mode(session_factory):
    """The exact bug: fake-mode runs used to leave no trace of having
    been fake at all -- `llm_mode` must be set at creation time, not
    guessed later."""
    async with session_factory() as session:
        instructor, manuscript, rubric = await _seed_instructor_and_pair(session)
        check_run = await create_check_run(
            session,
            instructor.id,
            manuscript.id,
            rubric.id,
            settings=Settings(veridical_fake_llm=True),
        )
        assert check_run.llm_mode.value == "fake"


async def test_create_check_run_persists_real_mode(session_factory):
    async with session_factory() as session:
        instructor, manuscript, rubric = await _seed_instructor_and_pair(session)
        check_run = await create_check_run(
            session,
            instructor.id,
            manuscript.id,
            rubric.id,
            settings=Settings(veridical_fake_llm=False),
        )
        assert check_run.llm_mode.value == "real"


async def _seed_done_flagged_run(session, *, fake: bool):
    instructor, manuscript, rubric = await _seed_instructor_and_pair(session)
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
    await session.refresh(criterion)

    check_run = await create_check_run(
        session, instructor.id, manuscript.id, rubric.id, settings=Settings(veridical_fake_llm=fake)
    )
    result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=None,
        kind=CheckKind.statistical_forensics,
        outcome=ResultOutcome.failed,
        score=0.0,
        detail={"reason": "n=5, M=4.20 (FIXTURE — Instructors) — p. 24"},
    )
    session.add(result)
    await session.commit()
    await session.refresh(result)
    flag = Flag(
        check_result_id=result.id,
        severity=FlagSeverity.med,
        evidence_excerpt="n=5, M=4.20 (FIXTURE — Instructors)",
        page_anchor="p. 24",
    )
    session.add(flag)
    check_run.status = CheckRunStatus.done
    await session.commit()
    await aggregate_and_score(session, check_run.id)
    return instructor.id, check_run.id, flag.id


async def test_report_discloses_fake_mode(session_factory):
    async with session_factory() as session:
        instructor_id, check_run_id, _ = await _seed_done_flagged_run(session, fake=True)
        report = await get_report(session, check_run_id, instructor_id)
        assert report.llm_mode == "fake"


async def test_report_discloses_real_mode(session_factory):
    async with session_factory() as session:
        instructor_id, check_run_id, _ = await _seed_done_flagged_run(session, fake=False)
        report = await get_report(session, check_run_id, instructor_id)
        assert report.llm_mode == "real"


async def test_flag_evidence_page_discloses_fake_mode(session_factory):
    """The audit's own reproduction: `/flags/{id}` rendered
    `n=5, M=4.20, SD=0.37 (Instructors) — p. 24` with nothing anywhere
    saying it came from a fixture."""
    async with session_factory() as session:
        instructor_id, _, flag_id = await _seed_done_flagged_run(session, fake=True)
        flag_out = await get_flag(session, flag_id, instructor_id)
        assert flag_out.llm_mode == "fake"


async def test_public_adviser_view_discloses_fake_mode(session_factory):
    """The audit named this the audience that matters most: an adviser
    reading a shared link has NO other context to catch a fabricated
    finding presented as real."""
    async with session_factory() as session:
        instructor_id, check_run_id, _ = await _seed_done_flagged_run(session, fake=True)
        link = await create_share_link(session, check_run_id, instructor_id, expires_at=None)
        shared = await get_shared_report(session, link.token)
        assert shared.report.llm_mode == "fake"


async def test_legacy_run_backfilled_to_unknown_not_claimed_real(session_factory):
    """Migration 0024's backfill: a check_run row that predates this
    column must not silently read as "real" -- that would be exactly the
    kind of confident-guess-dressed-as-fact the charter forbids (rule 9).
    Simulated here by inserting a CheckRun the way a pre-migration row
    would look: never explicitly given an llm_mode, so the column's own
    server_default applies."""
    async with session_factory() as session:
        instructor, manuscript, rubric = await _seed_instructor_and_pair(session)
        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        check_run.status = CheckRunStatus.done
        await session.commit()
        await session.refresh(check_run)
        await aggregate_and_score(session, check_run.id)
        report = await get_report(session, check_run.id, instructor.id)
        assert report.llm_mode == "unknown"
