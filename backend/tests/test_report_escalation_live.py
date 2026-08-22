"""V-023 live-DB tests: the escalated panel + resolution flow — integration
level (seeded disagreement → escalation → resolve → status change), plus
the charter rule 1 guard (no code path auto-decides a low-confidence item).
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.errors import ConflictError, NotFoundError
from app.models.enums import CheckKind, CheckRunStatus, ReadinessStatus, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, ReadinessReport
from app.report.service import (
    aggregate_and_score,
    decide_report,
    list_escalated_for_run,
    resolve_escalation_for_run,
)

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_escalationtest"


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
                "TRUNCATE audit_log, readiness_report, check_result, check_run, "
                "criterion, rubric, manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _seed_escalated_run(session, *, ai_majority_verdict="pass", agreement=0.667):
    """One check_run with one semantic criterion sitting at `escalated`,
    shaped exactly like `app.checks.consistency`'s own persisted output
    (agreement + votes + a majority verdict pending instructor review)."""
    instructor = Instructor(email=f"esc-{id(session)}@test.local", display_name="Esc Test")
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
    detail = {
        "basis": "llm",
        "agreement": agreement,
        "votes": ["pass", "fail", ai_majority_verdict] if ai_majority_verdict else ["fail", "pass"],
        "context_label": "CHAPTER 1",
        "prompt_version": "v1",
    }
    if ai_majority_verdict is not None:
        detail["verdict"] = ai_majority_verdict
        detail["reasoning"] = "Two of three passes agreed."
        detail["evidence"] = [{"quote": "The problem is stated.", "anchor": "page 3"}]
    check_result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=criterion.id,
        kind=CheckKind.semantic,
        outcome=ResultOutcome.escalated,
        score=None,
        detail=detail,
    )
    session.add(check_result)
    await session.commit()
    await aggregate_and_score(session, check_run.id)
    return instructor, check_run, check_result


async def test_escalated_item_excluded_from_score_until_resolved(session_factory):
    async with session_factory() as session:
        instructor, check_run, _ = await _seed_escalated_run(session)
        panel = await list_escalated_for_run(session, check_run.id, instructor.id)
        assert len(panel) == 1
        assert panel[0].agreement == pytest.approx(0.667, abs=0.001)
        assert panel[0].ai_majority_verdict == "pass"

        report = await session.scalar(
            select(ReadinessReport).where(ReadinessReport.check_run_id == check_run.id)
        )
        # Nothing decidable exists yet — an honest needs_review, no score
        # fabricated (charter rule 9), same as V-019's all-escalated case.
        assert report.status == ReadinessStatus.needs_review
        assert report.composite_score is None


async def test_resolve_accept_majority_updates_score_and_status_live(session_factory):
    async with session_factory() as session:
        instructor, check_run, check_result = await _seed_escalated_run(session)
        out = await resolve_escalation_for_run(
            session,
            check_run.id,
            check_result.id,
            instructor.id,
            "accept_majority",
            "Agreed with the AI's majority call after reading the excerpt.",
        )
        assert out.outcome == "passed"
        assert out.score == 100.0
        # Live recompute: single 100%-weight criterion now passes → Ready.
        assert out.report.status == "ready"
        assert out.report.composite_score == 100.0

        panel = await list_escalated_for_run(session, check_run.id, instructor.id)
        assert panel == []  # resolved — no longer sitting in the panel


async def test_resolve_mark_fail_overrides_the_ai_majority(session_factory):
    async with session_factory() as session:
        instructor, check_run, check_result = await _seed_escalated_run(
            session, ai_majority_verdict="pass"
        )
        out = await resolve_escalation_for_run(
            session,
            check_run.id,
            check_result.id,
            instructor.id,
            "mark_fail",
            "Checked myself — it doesn't.",
        )
        assert out.outcome == "failed"
        assert out.score == 0.0
        assert out.report.status == "not_ready"

        # Original AI call preserved, never destroyed — both sides visible.
        result = out.report.results[0]
        assert result.reasoning == "Two of three passes agreed."  # AI's own reasoning intact
        assert result.outcome == "failed"  # but the FINAL outcome reflects the override


async def test_resolve_needs_document_excludes_without_blocking_decision(session_factory):
    """V-068 AC2, DECIDED 2026-08-16: excludes the criterion from the
    composite like `not_applicable` already behaves (never a 0, never a
    pass), and does not block the decision — a third option that is
    honest about not being a guess, instead of forcing Pass/Fail on
    evidence the instructor never saw."""
    async with session_factory() as session:
        instructor, check_run, check_result = await _seed_escalated_run(session)
        out = await resolve_escalation_for_run(
            session,
            check_run.id,
            check_result.id,
            instructor.id,
            "needs_document",
            "Cannot verify this without opening the manuscript myself.",
        )
        assert out.outcome == "not_applicable"
        assert out.score is None
        # Single 100%-weight criterion now excluded entirely -- same
        # honest needs_review a zero-decidable-weight run already produces
        # (V-019), never a fabricated 0 or 100.
        assert out.report.status == "needs_review"
        assert out.report.composite_score is None
        assert out.report.pending_review_count == 0  # never blocks the decision

        panel = await list_escalated_for_run(session, check_run.id, instructor.id)
        assert panel == []  # resolved — no longer sitting in the panel

        # Would raise ConflictError ("still need your review") if this
        # resolution were still counted as pending.
        report = await decide_report(
            session,
            check_run.id,
            instructor.id,
            "approved",
            "Approved despite the unresolved evidence gap on one criterion.",
        )
        assert report.decision == "approved"


async def test_accept_majority_without_an_ai_majority_is_rejected(session_factory):
    async with session_factory() as session:
        instructor, check_run, check_result = await _seed_escalated_run(
            session, ai_majority_verdict=None, agreement=0.333
        )
        with pytest.raises(ConflictError):
            await resolve_escalation_for_run(
                session, check_run.id, check_result.id, instructor.id, "accept_majority", "reason"
            )


async def test_resolving_an_already_resolved_item_is_rejected(session_factory):
    async with session_factory() as session:
        instructor, check_run, check_result = await _seed_escalated_run(session)
        await resolve_escalation_for_run(
            session, check_run.id, check_result.id, instructor.id, "mark_pass", "first resolution"
        )
        with pytest.raises(ConflictError):
            await resolve_escalation_for_run(
                session, check_run.id, check_result.id, instructor.id, "mark_pass", "second attempt"
            )


async def test_unverified_evidence_survives_to_the_panel(session_factory):
    """V-068 Q1/Q2: quotes the model returned but that failed containment
    verification must reach the panel (never merged into a verified
    `evidence` list -- no real anchor exists for one)."""
    async with session_factory() as session:
        instructor, check_run, _ = await _seed_escalated_run(session, ai_majority_verdict=None)
        check_result = (
            await session.execute(
                select(CheckResult).where(CheckResult.check_run_id == check_run.id)
            )
        ).scalar_one()
        detail = dict(check_result.detail)
        detail["unverified_evidence"] = ["a quote that could not be verified"]
        check_result.detail = detail
        await session.commit()

        panel = await list_escalated_for_run(session, check_run.id, instructor.id)
        assert panel[0].unverified_evidence == ["a quote that could not be verified"]


async def test_cross_instructor_access_is_rejected_not_leaked(session_factory):
    async with session_factory() as session:
        _, check_run, check_result = await _seed_escalated_run(session)
        stranger = Instructor(email="stranger@test.local", display_name="Stranger")
        session.add(stranger)
        await session.commit()

        with pytest.raises(NotFoundError):
            await list_escalated_for_run(session, check_run.id, stranger.id)
        with pytest.raises(NotFoundError):
            await resolve_escalation_for_run(
                session, check_run.id, check_result.id, stranger.id, "mark_pass", "reason"
            )
