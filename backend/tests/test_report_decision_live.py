"""V-038 live-DB tests: the terminal decision gate (F8.5) — approve/return/
reject, blocked while criteria still need review, frozen until an explicit
reasoned reopen, and the superseded-rubric warning signal.
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.errors import ConflictError, NotFoundError
from app.models.enums import CheckKind, CheckRunStatus, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, ReadinessReport
from app.report.service import (
    aggregate_and_score,
    decide_report,
    get_report,
    reopen_report,
    resolve_escalation_for_run,
)

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_decisiontest"


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


async def _seed_decidable_run(session, *, rubric_is_active=True):
    """A DONE run with exactly one decidable (passed) criterion — nothing
    escalated, so decision is never blocked unless a test adds one."""
    instructor = Instructor(email=f"dec-{id(session)}@test.local", display_name="Dec Test")
    session.add(instructor)
    await session.commit()
    manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
    rubric = Rubric(
        instructor_id=instructor.id,
        title="Format",
        source_file="r.pdf",
        is_active=rubric_is_active,
    )
    session.add_all([manuscript, rubric])
    await session.commit()
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
    check_run = CheckRun(
        manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
    )
    session.add(check_run)
    await session.commit()
    check_result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=criterion.id,
        kind=CheckKind.structural,
        outcome=ResultOutcome.passed,
        score=Decimal("100"),
        detail={"basis": "rule"},
    )
    session.add(check_result)
    await session.commit()
    await aggregate_and_score(session, check_run.id)
    return instructor, check_run


async def _seed_not_ready_run(session):
    """A DONE run with exactly one decidable (FAILED) criterion -- scores
    0, well below the default `not_ready_max_score` floor, so its status
    is `not_ready`. BUG-095's test fixture: approving this without a
    reason must be rejected."""
    instructor = Instructor(email=f"notready-{id(session)}@test.local", display_name="NR Test")
    session.add(instructor)
    await session.commit()
    manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
    rubric = Rubric(
        instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=True
    )
    session.add_all([manuscript, rubric])
    await session.commit()
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
    check_run = CheckRun(
        manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
    )
    session.add(check_run)
    await session.commit()
    check_result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=criterion.id,
        kind=CheckKind.structural,
        outcome=ResultOutcome.failed,
        score=Decimal("0"),
        detail={"basis": "rule"},
    )
    session.add(check_result)
    await session.commit()
    await aggregate_and_score(session, check_run.id)
    return instructor, check_run


async def _seed_needs_review_run(session):
    """A DONE run whose only criterion is `not_applicable` -- excluded
    from scoring (`scoring.py`'s `weight_sum <= 0` short-circuit), so
    NOTHING is computed at all: `composite_score=None`,
    `status=needs_review`. Not in `NEEDS_REVIEW_OUTCOMES` (that's
    escalated/quota_exhausted/api_down only), so the earlier `pending`
    gate does NOT block deciding this -- `DECISIONS_REQUIRING_A_REASON`
    is the only thing standing between this state and a one-click,
    zero-signal, zero-explanation decision (backend-critic finding,
    BUG-095 follow-up)."""
    instructor = Instructor(email=f"needsreview-{id(session)}@test.local", display_name="NR2 Test")
    session.add(instructor)
    await session.commit()
    manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
    rubric = Rubric(
        instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=True
    )
    session.add_all([manuscript, rubric])
    await session.commit()
    criterion = Criterion(
        rubric_id=rubric.id,
        type="structural",
        text="Uses a rubric-external logbook the manuscript can't demonstrate",
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
    check_result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=criterion.id,
        kind=CheckKind.structural,
        outcome=ResultOutcome.not_applicable,
        score=None,
        detail={"basis": "rule"},
    )
    session.add(check_result)
    await session.commit()
    await aggregate_and_score(session, check_run.id)
    return instructor, check_run


async def _add_escalated_criterion(session, check_run):
    criterion = Criterion(
        rubric_id=check_run.rubric_id,
        type="semantic",
        text="Chapter 1 states the problem",
        evidence=None,
        weight=Decimal("50"),
        position=1,
    )
    session.add(criterion)
    await session.commit()
    check_result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=criterion.id,
        kind=CheckKind.semantic,
        outcome=ResultOutcome.escalated,
        score=None,
        detail={"basis": "llm", "agreement": 0.5, "votes": ["pass", "fail"]},
    )
    session.add(check_result)
    await session.commit()
    await aggregate_and_score(session, check_run.id)


async def test_decides_and_freezes_the_report(session_factory):
    async with session_factory() as session:
        instructor, check_run = await _seed_decidable_run(session)
        report = await decide_report(
            session, check_run.id, instructor.id, "approved", "Looks ready."
        )
        assert report.decision == "approved"
        assert report.decision_note == "Looks ready."
        assert report.decided_at is not None

        row = await session.scalar(
            select(ReadinessReport).where(ReadinessReport.check_run_id == check_run.id)
        )
        assert row.decision.value == "approved"
        assert row.decided_by_instructor_id == instructor.id


async def test_note_is_optional(session_factory):
    """BUG-095: a note is optional when the decision AGREES with
    VERIDICAL's own computed verdict -- `_seed_decidable_run` produces a
    fully-passing, `ready` report, so approving it is the agreeing case.
    Disagreeing decisions (e.g. rejecting a `ready` report) require a
    reason -- see `test_deciding_against_the_verdict_requires_a_reason`
    below."""
    async with session_factory() as session:
        instructor, check_run = await _seed_decidable_run(session)
        report = await decide_report(session, check_run.id, instructor.id, "approved", None)
        assert report.decision == "approved"
        assert report.decision_note is None


async def test_blank_note_normalizes_to_none(session_factory):
    async with session_factory() as session:
        instructor, check_run = await _seed_decidable_run(session)
        report = await decide_report(session, check_run.id, instructor.id, "returned", "   ")
        assert report.decision_note is None


async def test_deciding_with_unresolved_escalations_is_blocked_with_the_count(session_factory):
    async with session_factory() as session:
        instructor, check_run = await _seed_decidable_run(session)
        await _add_escalated_criterion(session, check_run)
        with pytest.raises(ConflictError, match="1 criterion"):
            await decide_report(session, check_run.id, instructor.id, "approved", None)

        # Confirmed still undecided — the block is real, not advisory.
        report = await get_report(session, check_run.id, instructor.id)
        assert report.decision is None
        assert report.pending_review_count == 1


async def test_deciding_an_already_decided_report_is_rejected(session_factory):
    async with session_factory() as session:
        instructor, check_run = await _seed_decidable_run(session)
        await decide_report(session, check_run.id, instructor.id, "approved", None)
        with pytest.raises(ConflictError):
            await decide_report(session, check_run.id, instructor.id, "rejected", None)


async def test_approving_a_not_ready_report_without_a_reason_is_rejected(session_factory):
    """BUG-095: approving a manuscript VERIDICAL itself scored `not_ready`
    used to need no reason at all -- the single highest-stakes,
    most panel-visible action in the product, while overriding one
    low-severity flag or resolving one escalation both already required
    one."""
    async with session_factory() as session:
        instructor, check_run = await _seed_not_ready_run(session)
        with pytest.raises(ConflictError, match="disagrees"):
            await decide_report(session, check_run.id, instructor.id, "approved", None)

        # Confirmed still undecided -- the block is real, not advisory.
        report = await get_report(session, check_run.id, instructor.id)
        assert report.decision is None


async def test_approving_a_not_ready_report_with_a_reason_succeeds(session_factory):
    async with session_factory() as session:
        instructor, check_run = await _seed_not_ready_run(session)
        report = await decide_report(
            session,
            check_run.id,
            instructor.id,
            "approved",
            "Panel already reviewed the flagged sections and cleared them in person.",
        )
        assert report.decision == "approved"
        assert report.decision_note == (
            "Panel already reviewed the flagged sections and cleared them in person."
        )


async def test_rejecting_a_ready_report_without_a_reason_is_rejected(session_factory):
    async with session_factory() as session:
        instructor, check_run = await _seed_decidable_run(session)  # status: ready
        with pytest.raises(ConflictError, match="disagrees"):
            await decide_report(session, check_run.id, instructor.id, "rejected", None)


async def test_approving_a_ready_report_without_a_reason_is_allowed(session_factory):
    """The point is capturing DISAGREEMENT with the system, not demanding
    a reason for every decision -- when decision and verdict agree, a
    note stays optional (ground rule 1: the human's judgment is the
    valuable signal, not paperwork for its own sake)."""
    async with session_factory() as session:
        instructor, check_run = await _seed_decidable_run(session)  # status: ready
        report = await decide_report(session, check_run.id, instructor.id, "approved", None)
        assert report.decision == "approved"
        assert report.decision_note is None


async def test_returning_a_report_for_revision_never_requires_a_reason(session_factory):
    """ "Returned" isn't a claim of agreement OR disagreement with the
    verdict -- it's neither an approval nor a rejection, so it's never in
    `DECISIONS_REQUIRING_A_REASON` regardless of status."""
    async with session_factory() as session:
        instructor, check_run = await _seed_not_ready_run(session)
        report = await decide_report(session, check_run.id, instructor.id, "returned", None)
        assert report.decision == "returned"


async def test_approving_or_rejecting_a_needs_review_report_requires_a_reason(session_factory):
    """`backend-critic` finding, live-reproduced: `needs_review` was
    missing entirely from `DECISIONS_REQUIRING_A_REASON` -- that status
    means composite_score is None, nothing was computed at all, so EITHER
    decision on it is pure human judgment with zero AI signal behind it."""
    async with session_factory() as session:
        instructor, check_run = await _seed_needs_review_run(session)
        report = await get_report(session, check_run.id, instructor.id)
        assert report.status == "needs_review"
        assert report.composite_score is None

        with pytest.raises(ConflictError, match="disagrees"):
            await decide_report(session, check_run.id, instructor.id, "approved", None)

    async with session_factory() as session:
        instructor, check_run = await _seed_needs_review_run(session)
        with pytest.raises(ConflictError, match="disagrees"):
            await decide_report(session, check_run.id, instructor.id, "rejected", None)


async def test_a_short_reason_is_rejected_when_a_reason_is_required(session_factory):
    """`newcomer`/`backend-critic` finding, live-reproduced: this used to
    check presence only, so "ok" satisfied it -- the escalation-
    resolution reason (BUG-096) enforces a real minimum on the exact same
    class of published justification; this is the SAME control on the
    higher-stakes action and had a lower bar than the one it trained the
    instructor to expect two clicks earlier."""
    async with session_factory() as session:
        instructor, check_run = await _seed_not_ready_run(session)
        with pytest.raises(ConflictError, match="at least 10 characters"):
            await decide_report(session, check_run.id, instructor.id, "approved", "ok")

        report = await get_report(session, check_run.id, instructor.id)
        assert report.decision is None


async def test_resolving_an_escalation_is_blocked_once_the_report_is_decided(session_factory):
    """`backend-critic` finding: `decide_report`'s own gate ensures no
    escalation is pending AT decide time, but that alone doesn't stop a
    LATER resolve from silently recomputing and persisting a new score
    onto an already-decided report -- `raise_if_decided` must be enforced
    inside `resolve_escalation_for_run` itself, not just at the decide
    boundary."""
    async with session_factory() as session:
        instructor, check_run = await _seed_decidable_run(session)
        await decide_report(session, check_run.id, instructor.id, "approved", None)
        await _add_escalated_criterion(session, check_run)

        escalated_result_id = await session.scalar(
            select(CheckResult.id).where(
                CheckResult.check_run_id == check_run.id,
                CheckResult.outcome == ResultOutcome.escalated,
            )
        )
        with pytest.raises(ConflictError):
            await resolve_escalation_for_run(
                session, check_run.id, escalated_result_id, instructor.id, "mark_pass", "reason"
            )


async def test_reopen_clears_the_decision_and_requires_a_reason(session_factory):
    async with session_factory() as session:
        instructor, check_run = await _seed_decidable_run(session)
        await decide_report(session, check_run.id, instructor.id, "approved", "Initial call.")

        report = await reopen_report(
            session, check_run.id, instructor.id, "Found a citation issue after deciding."
        )
        assert report.decision is None
        assert report.decision_note is None
        assert report.decided_at is None

        # Reopened -> can be decided again (not permanently locked).
        redecided = await decide_report(session, check_run.id, instructor.id, "returned", None)
        assert redecided.decision == "returned"


async def test_reopening_a_never_decided_report_is_rejected(session_factory):
    async with session_factory() as session:
        instructor, check_run = await _seed_decidable_run(session)
        with pytest.raises(ConflictError):
            await reopen_report(session, check_run.id, instructor.id, "reason")


async def test_reopen_writes_a_distinct_audit_event_preserving_the_prior_decision(session_factory):
    async with session_factory() as session:
        instructor, check_run = await _seed_decidable_run(session)
        # BUG-095: rejecting a `ready` report (`_seed_decidable_run`'s
        # status) disagrees with VERIDICAL's own verdict and now requires
        # a reason -- unrelated to what THIS test asserts (the reopen
        # audit trail), so a real note here, not None.
        await decide_report(
            session, check_run.id, instructor.id, "rejected", "Found a citation issue on review."
        )
        await reopen_report(session, check_run.id, instructor.id, "Instructor changed their mind.")

        from app.models.audit import AuditLog

        rows = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.check_run_id == check_run.id)
                    .order_by(AuditLog.id)
                )
            )
            .scalars()
            .all()
        )
        event_types = [r.event_type for r in rows]
        assert "report_decided" in event_types
        assert "report_reopened" in event_types
        reopened = next(r for r in rows if r.event_type == "report_reopened")
        assert reopened.payload["prior_decision"] == "rejected"
        assert reopened.payload["reason"] == "Instructor changed their mind."


async def test_rubric_is_current_reflects_a_superseded_version(session_factory):
    async with session_factory() as session:
        instructor, check_run = await _seed_decidable_run(session, rubric_is_active=False)
        report = await get_report(session, check_run.id, instructor.id)
        assert report.rubric_is_current is False


async def test_cross_instructor_cannot_decide_or_reopen(session_factory):
    async with session_factory() as session:
        _, check_run = await _seed_decidable_run(session)
        stranger = Instructor(email="stranger-dec@test.local", display_name="Stranger")
        session.add(stranger)
        await session.commit()

        with pytest.raises(NotFoundError):
            await decide_report(session, check_run.id, stranger.id, "approved", None)
        with pytest.raises(NotFoundError):
            await reopen_report(session, check_run.id, stranger.id, "reason")
