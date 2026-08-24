"""V-015 live-DB tests: apply_routing persists the audit trail + the
terminal not_applicable check_result for unroutable criteria. Needs a live
Postgres (same convention as test_rubric_versioning.py); own scratch DB.
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.checks.router import apply_routing, route_criteria
from app.models.audit import AuditLog
from app.models.enums import CheckKind, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_checkroutertest"


@pytest.fixture(scope="module")
def router_scratch_url():
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
def session_factory(router_scratch_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url

    engine = create_async_engine(sqlalchemy_url(router_scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean_tables(session_factory):
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE audit_log, check_result, check_run, criterion, rubric, "
                "manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _seed_run(
    session_factory, criterion_specs: list[tuple[str, str]]
) -> tuple[int, list[int]]:
    """criterion_specs: list of (type, text). Returns (check_run_id, criterion_ids)."""
    async with session_factory() as session:
        instructor = Instructor(email="router@demo.local", display_name="Router Test")
        session.add(instructor)
        await session.commit()
        await session.refresh(instructor)

        manuscript = Manuscript(
            instructor_id=instructor.id, group_label="Group A", file_ref="x.pdf"
        )
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()
        await session.refresh(manuscript)
        await session.refresh(rubric)

        criteria = [
            Criterion(
                rubric_id=rubric.id,
                type=ctype,
                text=ctext,
                evidence=None,
                weight=Decimal("10"),
                position=i,
            )
            for i, (ctype, ctext) in enumerate(criterion_specs)
        ]
        session.add_all(criteria)
        await session.commit()
        for c in criteria:
            await session.refresh(c)

        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        await session.commit()
        await session.refresh(check_run)

        return check_run.id, [c.id for c in criteria]


async def test_routing_decisions_are_visible_in_audit_log(session_factory):
    check_run_id, criterion_ids = await _seed_run(
        session_factory,
        [("semantic", "Argument well developed"), ("structural", "Has an abstract")],
    )
    async with session_factory() as session:
        criteria = (
            (await session.execute(select(Criterion).where(Criterion.id.in_(criterion_ids))))
            .scalars()
            .all()
        )
        decisions = route_criteria(criteria)
        await apply_routing(session, check_run_id, decisions)

    async with session_factory() as session:
        rows = (
            (await session.execute(select(AuditLog).where(AuditLog.check_run_id == check_run_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].event_type == "criterion_routing"
    assert len(rows[0].payload["decisions"]) == 2


async def test_unroutable_criterion_persists_not_applicable_never_dropped(session_factory):
    """A criterion with a corrupted/unrecognized type (bypassing the ORM
    enum by writing raw SQL, simulating stale data) still ends up with a
    real check_result — never silently absent."""
    check_run_id, criterion_ids = await _seed_run(session_factory, [("semantic", "Normal one")])
    async with session_factory() as session:
        await session.execute(
            text("UPDATE criterion SET type = 'weird' WHERE id = :id"),
            {"id": criterion_ids[0]},
        )
        await session.commit()

    async with session_factory() as session:
        # Read the raw type back without triggering SQLAlchemy enum
        # validation (native_enum=False means this is just a VARCHAR read).
        raw = (
            await session.execute(
                text("SELECT id, type, text FROM criterion WHERE id = :id"),
                {"id": criterion_ids[0]},
            )
        ).one()

        from app.checks.router import route_criterion

        class _Row:
            def __init__(self, text_: str):
                self.text = text_
                self.evidence = None

        decision = route_criterion(_Row(raw.text), criterion_id=raw.id, raw_type=raw.type)
        assert decision.unroutable
        await apply_routing(session, check_run_id, [decision])

    async with session_factory() as session:
        result = (
            await session.execute(
                select(CheckResult).where(CheckResult.criterion_id == criterion_ids[0])
            )
        ).scalar_one()
    assert result.outcome == ResultOutcome.not_applicable
    assert result.kind == CheckKind.semantic
    assert result.detail["reason"]


async def test_not_assessable_criterion_persists_not_applicable_never_ai_graded(session_factory):
    """BUG-092, end to end: a real defense-day criterion (the ticket's
    own example) never touches AI grading and never appears in the
    escalation queue -- it gets a terminal, honest `not_applicable`
    result directly, the same persistence path as an unroutable
    criterion, but via a real `CriterionType` value, not a corrupted one."""
    check_run_id, criterion_ids = await _seed_run(
        session_factory,
        [("not_assessable", "The group brings three bound copies of the paper to the defense.")],
    )
    async with session_factory() as session:
        criteria = (
            await session.execute(select(Criterion).where(Criterion.id == criterion_ids[0]))
        ).scalars()
        decisions = route_criteria(list(criteria))
        assert decisions[0].unroutable
        await apply_routing(session, check_run_id, decisions)

    async with session_factory() as session:
        result = (
            await session.execute(
                select(CheckResult).where(CheckResult.criterion_id == criterion_ids[0])
            )
        ).scalar_one()
    assert result.outcome == ResultOutcome.not_applicable
    assert "cannot check this from the document" in result.detail["reason"]

    async with session_factory() as session:
        from app.checks.escalation import list_escalated

        escalated = await list_escalated(session, check_run_id)
    assert escalated == []
