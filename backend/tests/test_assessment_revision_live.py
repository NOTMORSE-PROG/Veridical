"""Versioned instructor actions against real PostgreSQL, including lock races."""

import asyncio
import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.db import sqlalchemy_url
from app.errors import ConflictError
from app.flags.service import annotate_flag, override_flag
from app.models.audit import AuditLog
from app.models.enums import (
    CheckKind,
    CheckRunStatus,
    CriterionType,
    FlagSeverity,
    ReadinessStatus,
    ResultOutcome,
)
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, Flag, ReadinessReport
from app.report.service import decide_report, get_report, reopen_report, resolve_escalation_for_run
from tests.test_schema import _admin_execute, _alembic_config, _swap_db

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="integration: owned Postgres required"
)
SCRATCH_DB = "veridical_assessment_actions_test"
REASON = "I inspected the manuscript evidence before recording this judgment."


@pytest.fixture(scope="module")
def scratch_url():
    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    url = _swap_db(base, SCRATCH_DB)
    try:
        command.upgrade(_alembic_config(url), "head")
        yield url
    finally:
        asyncio.run(_admin_execute(base, f'DROP DATABASE "{SCRATCH_DB}" WITH (FORCE)'))


@pytest.fixture()
async def factory(scratch_url):
    engine = create_async_engine(sqlalchemy_url(scratch_url))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def seed(factory):
    async with factory() as session:
        owner = Instructor(email=f"assessment-{uuid4()}@test.local", display_name="Instructor")
        session.add(owner)
        await session.flush()
        rubric = Rubric(instructor_id=owner.id, title="Custom format", source_file="format.pdf")
        manuscript = Manuscript(
            instructor_id=owner.id, group_label="Assessment control", file_ref="source.pdf"
        )
        session.add_all([rubric, manuscript])
        await session.flush()
        run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done)
        session.add(run)
        await session.flush()
        criteria = [
            Criterion(
                rubric_id=rubric.id,
                type=CriterionType.semantic,
                text="Explain the design",
                weight=Decimal("1"),
                position=0,
            ),
            Criterion(
                rubric_id=rubric.id,
                type=CriterionType.semantic,
                text="Evaluate the evidence",
                weight=Decimal("1"),
                position=1,
                levels=[
                    {
                        "level": 1,
                        "name": "Absent",
                        "descriptor": "No supported result",
                        "points": 0,
                    },
                    {
                        "level": 2,
                        "name": "Developing",
                        "descriptor": "Some supported result",
                        "points": 1,
                    },
                    {
                        "level": 3,
                        "name": "Complete",
                        "descriptor": "Complete supported result",
                        "points": 3,
                    },
                ],
            ),
        ]
        session.add_all(criteria)
        await session.flush()
        results = [
            CheckResult(
                check_run_id=run.id,
                criterion_id=c.id,
                kind=CheckKind.semantic,
                outcome=ResultOutcome.escalated,
                detail={
                    "basis": "llm",
                    "verdict": None,
                    "reason": "Recorded uncertainty",
                    "votes": [],
                    "agreement": 0,
                },
            )
            for c in criteria
        ]
        session.add_all(results)
        integrity = CheckResult(
            check_run_id=run.id,
            kind=CheckKind.internal_agreement,
            outcome=ResultOutcome.passed,
            detail={},
        )
        session.add(integrity)
        await session.flush()
        flag = Flag(
            check_result_id=integrity.id,
            severity=FlagSeverity.med,
            evidence_excerpt="Recorded possible inconsistency",
            page_anchor="page 3",
        )
        session.add_all(
            [flag, ReadinessReport(check_run_id=run.id, status=ReadinessStatus.needs_review)]
        )
        await session.commit()
        return owner.id, run.id, [r.id for r in results], flag.id


async def stored(factory, run_id):
    async with factory() as session:
        parent = await session.scalar(
            select(ReadinessReport).where(ReadinessReport.check_run_id == run_id)
        )
        rows = (
            await session.scalars(
                select(CheckResult)
                .where(CheckResult.check_run_id == run_id, CheckResult.criterion_id.isnot(None))
                .order_by(CheckResult.id)
            )
        ).all()
        audits = (
            await session.scalars(
                select(AuditLog).where(AuditLog.check_run_id == run_id).order_by(AuditLog.id)
            )
        ).all()
        return parent, rows, audits


async def test_return_preserves_pending_freezes_and_reopens_before_resolution(factory):
    owner, run, ids, flag = await seed(factory)
    for decision in ("approved", "rejected"):
        async with factory() as session:
            with pytest.raises(ConflictError, match="before approval or rejection"):
                await decide_report(session, run, owner, decision, REASON, expected_revision=0)
    async with factory() as session:
        with pytest.raises(ConflictError, match="returned for revision"):
            await decide_report(session, run, owner, "returned", "short", expected_revision=0)
        out = await decide_report(session, run, owner, "returned", REASON, expected_revision=0)
    assert out.assessment_revision == 1 and out.decision == "returned"
    assert out.pending_review_count == 2 and out.composite_score is None
    parent, rows, audits = await stored(factory, run)
    assert all(
        r.outcome == ResultOutcome.escalated and r.score is None and "resolution" not in r.detail
        for r in rows
    )
    decision_audit = next(a for a in audits if a.event_type == "report_decided")
    assert {row["check_result_id"] for row in decision_audit.payload["pending_criteria"]} == set(
        ids
    )
    assert decision_audit.payload["assessment_revision"] == 1
    for action in (
        lambda s: resolve_escalation_for_run(
            s, run, ids[0], owner, "mark_pass", REASON, expected_revision=1
        ),
        lambda s: override_flag(s, flag, owner, REASON, expected_revision=1),
    ):
        async with factory() as session:
            with pytest.raises(ConflictError, match="Reopen it first"):
                await action(session)
    async with factory() as session:
        annotated = await annotate_flag(session, flag, owner, "Independent review note")
        assert (
            annotated.annotation == "Independent review note" and annotated.assessment_revision == 1
        )
        await session.commit()
        reopened = await reopen_report(session, run, owner, REASON, expected_revision=1)
        assert reopened.assessment_revision == 2 and reopened.pending_review_count == 2
    async with factory() as session:
        first = await resolve_escalation_for_run(
            session, run, ids[0], owner, "mark_pass", REASON, expected_revision=2
        )
        assert first.report.assessment_revision == 3 and first.report.pending_review_count == 1
    async with factory() as session:
        with pytest.raises(ConflictError, match="changed after you reviewed"):
            await resolve_escalation_for_run(
                session, run, ids[1], owner, "mark_level", REASON, level=2, expected_revision=2
            )
        second = await resolve_escalation_for_run(
            session, run, ids[1], owner, "mark_level", REASON, level=2, expected_revision=3
        )
        assert second.score == 33.33 and second.report.assessment_revision == 4
        assert second.report.pending_review_count == 0
    _, rows, audits = await stored(factory, run)
    assert rows[1].score == Decimal("33.33")
    assert [a.payload["new_score"] for a in audits if a.event_type == "escalation_resolved"] == [
        100,
        33.33,
    ]


@pytest.mark.parametrize("token", [None, True, False, "0", 0.0, -1])
async def test_direct_authority_rejects_non_integer_or_negative_tokens(factory, token):
    owner, run, _, _ = await seed(factory)
    async with factory() as session:
        with pytest.raises(ConflictError, match="Reload and review"):
            await decide_report(session, run, owner, "returned", REASON, expected_revision=token)
    parent, _, audits = await stored(factory, run)
    assert parent.assessment_revision == 0 and parent.decision is None and not audits


async def test_resolution_rollback_includes_result_audit_aggregate_and_revision(
    factory, monkeypatch
):
    import app.report.service as service

    owner, run, ids, _ = await seed(factory)

    async def fail(*args, **kwargs):
        raise RuntimeError("Controlled aggregate failure")

    monkeypatch.setattr(service, "aggregate_and_score", fail)
    async with factory() as session:
        with pytest.raises(RuntimeError, match="Controlled aggregate"):
            await resolve_escalation_for_run(
                session, run, ids[0], owner, "mark_pass", REASON, expected_revision=0
            )
        await session.commit()  # A caller cannot accidentally persist a failed partial action.
    parent, rows, audits = await stored(factory, run)
    assert parent.assessment_revision == 0
    assert rows[0].outcome == ResultOutcome.escalated and rows[0].score is None
    assert "resolution" not in rows[0].detail and not audits


async def test_two_judgments_from_one_revision_have_one_winner(factory, monkeypatch):
    import app.report.service as service

    owner, run, ids, _ = await seed(factory)
    held, release = asyncio.Event(), asyncio.Event()
    actual = service.resolve_escalation

    async def pause(*args, **kwargs):
        if args[2] == ids[0]:
            held.set()
            await release.wait()
        return await actual(*args, **kwargs)

    monkeypatch.setattr(service, "resolve_escalation", pause)

    async def save(result, resolution, level=None):
        async with factory() as session:
            return await resolve_escalation_for_run(
                session, run, result, owner, resolution, REASON, level=level, expected_revision=0
            )

    first = asyncio.create_task(save(ids[0], "mark_pass"))
    await asyncio.wait_for(held.wait(), 5)
    second = asyncio.create_task(save(ids[1], "mark_level", 2))
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(second), 0.1)
    finally:
        release.set()
    one, two = await asyncio.gather(first, second, return_exceptions=True)
    assert not isinstance(one, BaseException) and isinstance(two, ConflictError)
    parent, rows, audits = await stored(factory, run)
    assert parent.assessment_revision == 1 and rows[1].outcome == ResultOutcome.escalated
    assert len([a for a in audits if a.event_type == "escalation_resolved"]) == 1


async def test_private_report_snapshot_blocks_writer_until_assembled(factory, monkeypatch):
    import app.report.service as service

    owner, run, ids, _ = await seed(factory)
    held, release = asyncio.Event(), asyncio.Event()
    actual = service._load_scorable_results
    pause_once = True

    async def pause(*args, **kwargs):
        nonlocal pause_once
        if pause_once:
            pause_once = False
            held.set()
            await release.wait()
        return await actual(*args, **kwargs)

    monkeypatch.setattr(service, "_load_scorable_results", pause)

    async def read():
        async with factory() as session:
            out = await get_report(session, run, owner)
            await session.commit()
            return out

    async def save():
        async with factory() as session:
            return await resolve_escalation_for_run(
                session, run, ids[0], owner, "mark_pass", REASON, expected_revision=0
            )

    reader = asyncio.create_task(read())
    await asyncio.wait_for(held.wait(), 5)
    writer = asyncio.create_task(save())
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(writer), 0.1)
    finally:
        release.set()
    before, after = await asyncio.gather(reader, writer)
    assert before.assessment_revision == 0 and before.pending_review_count == 2
    assert all(r.resolution is None for r in before.results)
    assert after.report.assessment_revision == 1 and after.report.pending_review_count == 1


async def test_acknowledgment_does_not_read_a_newer_sibling_after_commit(factory, monkeypatch):
    owner, run, ids, _ = await seed(factory)
    committed, release = asyncio.Event(), asyncio.Event()
    async with factory() as first_session:
        actual_commit = first_session.commit

        async def pause_commit():
            await actual_commit()
            committed.set()
            await release.wait()

        monkeypatch.setattr(first_session, "commit", pause_commit)
        first = asyncio.create_task(
            resolve_escalation_for_run(
                first_session, run, ids[0], owner, "mark_pass", REASON, expected_revision=0
            )
        )
        await asyncio.wait_for(committed.wait(), 5)
        try:
            async with factory() as second_session:
                newer = await resolve_escalation_for_run(
                    second_session,
                    run,
                    ids[1],
                    owner,
                    "mark_level",
                    REASON,
                    level=2,
                    expected_revision=1,
                )
        finally:
            release.set()
        original = await first
    assert original.report.assessment_revision == 1 and original.report.pending_review_count == 1
    assert newer.report.assessment_revision == 2 and newer.report.pending_review_count == 0
