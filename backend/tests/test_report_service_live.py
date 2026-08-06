"""V-019 live-DB test: `aggregate_and_score` persists a real
`readiness_report` row, and `build_report_payload` recomputes the same
explainable breakdown from the same data (must never disagree — both
call the identical pure `score_check_run`). Own scratch DB, same
convention as the other V2 live tests.
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.models.enums import CheckKind, CheckRunStatus, ReadinessStatus, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, ReadinessReport
from app.report.service import aggregate_and_score, build_report_payload, get_report

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_reporttest"


@pytest.fixture(scope="module")
def report_scratch_url():
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
def session_factory(report_scratch_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url

    engine = create_async_engine(sqlalchemy_url(report_scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean_tables(session_factory):
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE readiness_report, check_result, check_run, criterion, "
                "rubric, manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _seed(session_factory, weights_outcomes_scores):
    async with session_factory() as session:
        instructor = Instructor(email="report@demo.local", display_name="Report Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()

        criteria = [
            Criterion(
                rubric_id=rubric.id,
                type="structural",
                text=f"C{i}",
                evidence=None,
                weight=Decimal(str(w)),
                position=i,
            )
            for i, (w, _, _) in enumerate(weights_outcomes_scores)
        ]
        session.add_all(criteria)
        await session.commit()
        for c in criteria:
            await session.refresh(c)

        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        await session.commit()

        for criterion, (_, outcome, score) in zip(criteria, weights_outcomes_scores, strict=True):
            session.add(
                CheckResult(
                    check_run_id=check_run.id,
                    criterion_id=criterion.id,
                    kind=CheckKind.structural,
                    outcome=ResultOutcome(outcome),
                    score=score,
                )
            )
        await session.commit()
        return check_run.id


async def test_aggregate_and_score_persists_a_real_report(session_factory):
    check_run_id = await _seed(
        session_factory,
        [(70, "passed", 100.0), (30, "failed", 0.0)],
    )
    async with session_factory() as session:
        report = await aggregate_and_score(session, check_run_id)
        assert report.composite_score == Decimal("70.00")
        assert report.status == ReadinessStatus.conditionally_ready  # 60 <= 70 < 85

    async with session_factory() as verify_session:
        stored = (
            await verify_session.execute(
                select(ReadinessReport).where(ReadinessReport.check_run_id == check_run_id)
            )
        ).scalar_one()
        assert stored.composite_score == Decimal("70.00")


async def test_build_report_payload_matches_persisted_score(session_factory):
    check_run_id = await _seed(session_factory, [(100, "passed", 92.0)])
    async with session_factory() as session:
        report = await aggregate_and_score(session, check_run_id)
        payload = await build_report_payload(session, check_run_id)
        assert float(report.composite_score) == payload["composite_score"]
        assert report.status.value == payload["status"]
        assert payload["thresholds"]["ready_min_score"] == 85.0
        assert len(payload["contributions"]) == 1


async def test_all_escalated_run_persists_needs_review_with_null_score(session_factory):
    check_run_id = await _seed(session_factory, [(100, "escalated", None)])
    async with session_factory() as session:
        report = await aggregate_and_score(session, check_run_id)
        assert report.composite_score is None
        assert report.status == ReadinessStatus.needs_review

    async with session_factory() as verify_session:
        stored = (
            await verify_session.execute(
                select(ReadinessReport).where(ReadinessReport.check_run_id == check_run_id)
            )
        ).scalar_one()
        assert stored.composite_score is None


async def test_resolved_escalation_surfaces_the_instructor_decision_on_the_report(session_factory):
    """A human resolving an escalated criterion must be visible on the
    report as a human decision, not silently relabeled as an ordinary
    AI-graded row carrying the AI's own superseded failure text with no
    trace of the instructor's reason (V-055 review — this was previously
    dropped between the persisted `detail` JSON and the API response)."""
    from app.checks.escalation import RESOLUTION_MARK_PASS, resolve_escalation

    async with session_factory() as session:
        instructor = Instructor(email="resolve@demo.local", display_name="Resolver")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()
        criterion = Criterion(
            rubric_id=rubric.id,
            type="semantic",
            text="Some criterion",
            evidence=None,
            weight=Decimal("100"),
            position=0,
        )
        session.add(criterion)
        await session.commit()
        await session.refresh(criterion)  # coerces .type from a raw str into CriterionType
        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        await session.commit()
        result = CheckResult(
            check_run_id=check_run.id,
            criterion_id=criterion.id,
            kind=CheckKind.semantic,
            outcome=ResultOutcome.escalated,
            score=None,
            detail={
                "reason": "Could not verify the quoted evidence after a retry.",
                "votes": [None, None],
                "agreement": 0.0,
            },
        )
        session.add(result)
        await session.commit()
        await session.refresh(result)

        resolved = await resolve_escalation(
            session,
            check_run.id,
            result.id,
            instructor.id,
            RESOLUTION_MARK_PASS,
            "Verified manually against the source PDF.",
        )
        assert resolved.outcome == ResultOutcome.passed

        check_run.status = CheckRunStatus.done
        await session.commit()

        report = await get_report(session, check_run.id, instructor.id)
        row = report.results[0]
        assert row.resolution is not None
        assert row.resolution.type == RESOLUTION_MARK_PASS
        assert row.resolution.reason == "Verified manually against the source PDF."
        # The AI's original (now-superseded) text is still readable for
        # context, but `resolution` is what the frontend must check FIRST
        # so a human decision is never mislabeled as AI-graded.
        assert row.reason == "Could not verify the quoted evidence after a retry."
