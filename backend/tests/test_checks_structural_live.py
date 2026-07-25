"""V-016 live-DB test: the full DB-touching path — `build_rule_context`
(reads back V-004's raw store + V-006's citations) and
`run_structural_check` (persists the check_result) — against a real
Postgres. Same scratch-DB convention as test_checks_router_live.py.
"""

import os
import time
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.checks.rules.context import build_rule_context
from app.checks.rules.sections import REQUIRED_SECTION_RULE_ID
from app.checks.structural import run_structural_check
from app.config import get_settings
from app.models.enums import CheckKind, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun
from tests.test_ingest_pdf import PdfBuilder

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_structuraltest"


@pytest.fixture(scope="module")
def structural_scratch_url():
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
def session_factory(structural_scratch_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url

    engine = create_async_engine(sqlalchemy_url(structural_scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean_tables(session_factory):
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE check_result, check_run, criterion, rubric, citation, "
                "manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


def _abstract_pdf(tmp_path):
    b = PdfBuilder()
    b.new_page().line("A STUDY OF THINGS", size=16, bold=True)
    b.new_page().line("ABSTRACT", bold=True)
    b.line("This study examines things of interest to the field.")
    b.new_page().line("REFERENCES", bold=True)
    b.line(
        "Reyes, J. P., & Cruz, M. A. (2023). Assessing capstone readiness. "
        "Philippine Journal of Education, 12(3), 45-67."
    )
    b.line("Garcia, L. (2020). Understanding rubric design (2nd ed.). Academic Press.")
    return b.save(tmp_path / "abstract.pdf")


async def test_structural_check_persists_a_real_pass_from_a_real_manuscript(
    tmp_path, monkeypatch, session_factory
):
    from app.ingest.service import ingest_manuscript

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    settings = get_settings()
    pdf_path = _abstract_pdf(tmp_path)

    async with session_factory() as session:
        instructor = Instructor(
            email=f"structural-{time.time_ns()}@test.local", display_name="Structural Test"
        )
        session.add(instructor)
        await session.commit()

        manuscript = Manuscript(
            instructor_id=instructor.id, group_label="Group A", file_ref=str(pdf_path)
        )
        session.add(manuscript)
        await session.commit()
        await ingest_manuscript(session, manuscript, pdf_path, settings)

        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add(rubric)
        await session.commit()
        criterion = Criterion(
            rubric_id=rubric.id,
            type="structural",
            text="The manuscript must include an Abstract",
            evidence=None,
            weight=Decimal("10"),
            position=0,
        )
        session.add(criterion)
        await session.commit()

        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        await session.commit()

        ctx = await build_rule_context(session, manuscript, settings)
        assert len(ctx.citations) == 2  # V-006's real extraction, read back from the DB

        from app.checks.router import RouteDecision

        decision = RouteDecision(
            criterion_id=criterion.id,
            kind=CheckKind.structural,
            rule_id=REQUIRED_SECTION_RULE_ID,
            degraded=False,
            note=None,
        )
        result = await run_structural_check(
            session, check_run.id, criterion, criterion.id, decision, ctx
        )
        assert result.outcome == ResultOutcome.passed
        assert result.detail["anchor"] == "page 2"

    async with session_factory() as verify_session:
        stored = (
            await verify_session.execute(
                select(CheckResult).where(CheckResult.criterion_id == criterion.id)
            )
        ).scalar_one()
        assert stored.outcome == ResultOutcome.passed
        assert stored.kind == CheckKind.structural
        assert stored.score == 100.0
