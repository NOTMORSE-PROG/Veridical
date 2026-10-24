"""V-085 actual normalized grades, private history, stale edits and atomic rollback."""

import asyncio
import os
from copy import deepcopy

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.db import sqlalchemy_url
from app.errors import ConflictError, NotFoundError
from app.models.enums import ResultOutcome
from app.models.rubric import Criterion
from app.models.run import CheckResult, CheckRun
from app.report.judgment_schemas import JudgmentRevisionIn
from app.report.judgments import get_judgment_context, get_judgment_history, revise_judgment
from app.report.service import decide_report, reopen_report, resolve_escalation_for_run
from app.share.service import _to_public_report
from tests.test_assessment_revision_live import REASON, seed, stored
from tests.test_criterion_source_coverage import complete
from tests.test_schema import _admin_execute, _alembic_config, _swap_db

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="owned Postgres required")
SCRATCH_DB = "veridical_judgment_actions_aeac_test"
NEW_REASON = "I inspected further manuscript evidence and corrected this recorded judgment."


@pytest.fixture(scope="module")
def scratch_url():
    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    try:
        url = _swap_db(base, SCRATCH_DB)
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


async def initial(factory, *, levelled=False, accepted=None):
    owner, run, ids, flag = await seed(factory)
    result_id = ids[1 if levelled else 0]
    async with factory() as session:
        result = await session.get(CheckResult, result_id)
        criterion_id = result.criterion_id
        if accepted:
            result.detail = {**result.detail, "verdict": accepted}
            await session.commit()
        ack = await resolve_escalation_for_run(
            session,
            run,
            result_id,
            owner,
            "accept_majority" if accepted else "mark_level" if levelled else "mark_pass",
            REASON,
            expected_revision=0,
            level=2 if levelled else None,
        )
    return owner, run, criterion_id, result_id, flag, ack


def revision(*, epoch=1, token=1, resolution="mark_fail", reason=NEW_REASON, level=None):
    return JudgmentRevisionIn(
        expected_revision=epoch,
        expected_judgment_revision=token,
        resolution=resolution,
        reason=reason,
        level=level,
    )


async def history(factory, owner, run, criterion, epoch, token, **kwargs):
    async with factory() as session:
        return await get_judgment_history(
            session,
            run,
            criterion,
            owner,
            expected_revision=epoch,
            expected_judgment_revision=token,
            **kwargs,
        )


@pytest.mark.asyncio
async def test_actual_revision_ack_history_and_original_evidence_are_coherent(factory):
    owner, run, criterion, result_id, _, initial_ack = await initial(factory)
    assert initial_ack.report.results[0].check_result_id == result_id
    assert initial_ack.report.results[0].judgment_revision == 1
    baseline = await history(factory, owner, run, criterion, 1, 1)
    assert len(baseline.entries) == 1 and not baseline.history_incomplete
    assert baseline.entries[0].action == "initial_resolution"
    _, before_rows, _ = await stored(factory, run)
    original_detail = deepcopy(before_rows[0].detail)
    async with factory() as session:
        ack = await revise_judgment(session, run, criterion, owner, revision())
    assert ack.context.assessment_revision == ack.report.assessment_revision == 2
    assert ack.context.current.judgment_revision == 2
    assert ack.context.current.outcome == "failed" and ack.context.current.score == 0
    assert ack.entry.before.reason == REASON and ack.entry.after.reason == NEW_REASON
    assert ack.entry.performed_at == ack.entry.after.recorded_at
    assert ack.entry.performed_by == owner == ack.entry.after.actor_id
    assert ack.report.pending_review_count == initial_ack.report.pending_review_count == 1
    parent, after_rows, audits = await stored(factory, run)
    assert parent.assessment_revision == 2 and after_rows[0].judgment_revision == 2
    for key in ("votes", "verdict", "reason", "agreement", "original_result"):
        assert after_rows[0].detail[key] == original_detail[key]
    events = [a for a in audits if a.event_type in ("escalation_resolved", "judgment_revised")]
    assert len(events) == 2
    current_history = await history(factory, owner, run, criterion, 2, 2)
    assert current_history.entries[0] == ack.entry
    assert current_history.entries[1] == baseline.entries[0]
    public = _to_public_report(ack.report).model_dump(mode="json")
    assert NEW_REASON not in str(public) and REASON not in str(public)
    assert all(
        "judgment_revision" not in r and "check_result_id" not in r for r in public["results"]
    )
    assert ack.context.original_assessment.original_outcome == "escalated"
    assert ack.context.original_assessment.reason == "Recorded uncertainty"


@pytest.mark.asyncio
async def test_revision_preserves_recorded_source_limits_in_both_private_views(factory):
    owner, run, criterion, result_id, _, _ = await initial(factory)
    coverage = complete(
        2,
        0,
        [7],
        [
            {"paragraph": 7, "kind": "table_cell", "n_occurrences": 1},
            {"paragraph": 7, "kind": "table_cell", "n_occurrences": 1},
        ],
    )
    async with factory() as session:
        result = await session.get(CheckResult, result_id)
        result.detail = {**result.detail, "source_coverage": deepcopy(coverage)}
        await session.commit()
    async with factory() as session:
        before = await get_judgment_context(session, run, criterion, owner)
    assert before.original_assessment.source_coverage.model_dump() == coverage
    async with factory() as session:
        ack = await revise_judgment(session, run, criterion, owner, revision())
    assert (
        ack.context.original_assessment.source_coverage
        == before.original_assessment.source_coverage
    )
    row = next(value for value in ack.report.results if value.criterion_id == criterion)
    assert row.source_coverage.model_dump() == coverage
    _, rows, _ = await stored(factory, run)
    assert (
        next(value for value in rows if value.id == result_id).detail["source_coverage"] == coverage
    )
    public = _to_public_report(ack.report).model_dump(mode="json")
    assert all("source_coverage" not in value for value in public["results"])


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [None, "pass"])
async def test_no_change_including_accepted_verb_conversion_rolls_back_every_write(
    factory, accepted
):
    owner, run, criterion, _, _, _ = await initial(factory, accepted=accepted)
    parent, rows, audits = await stored(factory, run)
    before = (
        parent.assessment_revision,
        deepcopy(rows[0].detail),
        rows[0].judgment_revision,
        [(a.id, deepcopy(a.payload)) for a in audits],
    )
    async with factory() as session:
        with pytest.raises(ConflictError, match="Change the judgment"):
            await revise_judgment(
                session,
                run,
                criterion,
                owner,
                revision(resolution="mark_pass", reason="  " + REASON + "  "),
            )
    parent, rows, audits = await stored(factory, run)
    assert (
        parent.assessment_revision,
        rows[0].detail,
        rows[0].judgment_revision,
        [(a.id, a.payload) for a in audits],
    ) == before


@pytest.mark.asyncio
async def test_partial_acceptance_and_same_grade_new_reason_are_actual_changes(factory):
    owner, run, criterion, _, _, _ = await initial(factory, accepted="partial")
    async with factory() as session:
        ack = await revise_judgment(
            session, run, criterion, owner, revision(resolution="mark_pass", reason=REASON)
        )
    assert ack.entry.before.score == 50 and ack.entry.after.score == 100
    async with factory() as session:
        corrected = await revise_judgment(
            session, run, criterion, owner, revision(epoch=2, token=2, resolution="mark_pass")
        )
    assert corrected.entry.before.score == corrected.entry.after.score == 100
    assert corrected.entry.before.reason != corrected.entry.after.reason


@pytest.mark.asyncio
async def test_exclusion_transitions_are_ungraded_and_do_not_create_pending_work(factory):
    owner, run, criterion, _, _, _ = await initial(factory)
    async with factory() as session:
        excluded = await revise_judgment(
            session, run, criterion, owner, revision(resolution="needs_document")
        )
    assert excluded.context.current.outcome == "not_applicable"
    assert excluded.context.current.score is None and excluded.report.pending_review_count == 1
    async with factory() as session:
        graded = await revise_judgment(
            session, run, criterion, owner, revision(epoch=2, token=2, resolution="mark_pass")
        )
    assert graded.context.current.score == 100 and graded.report.pending_review_count == 1
    assert len((await history(factory, owner, run, criterion, 3, 3)).entries) == 3


@pytest.mark.asyncio
async def test_database_fraction_and_legacy_missing_rung_are_not_reconstructed(factory):
    owner, run, criterion, result_id, _, _ = await initial(factory, levelled=True)
    async with factory() as session:
        result = await session.get(CheckResult, result_id)
        result.detail = {**result.detail, "resolution": {"type": "mark_level", "reason": REASON}}
        result.detail.pop("level")
        result.detail.pop("judgment_history_incomplete")
        result.judgment_revision = 0
        await session.commit()
    async with factory() as session:
        current = await get_judgment_context(session, run, criterion, owner)
    assert current.current.level is None and current.current.score == 33.33
    assert current.current.actor_id is None and current.current.recorded_at is None
    async with factory() as session:
        ack = await revise_judgment(
            session, run, criterion, owner, revision(token=0, resolution="mark_level", level=3)
        )
    assert ack.entry.before.level is None and ack.entry.before.score == 33.33
    assert ack.entry.after.level.ordinal == 3
    assert (await history(factory, owner, run, criterion, 2, 1)).history_incomplete


@pytest.mark.asyncio
async def test_two_tabs_have_one_winner_and_stale_replay_has_no_second_audit(factory):
    owner, run, criterion, _, _, _ = await initial(factory)

    async def save(reason):
        async with factory() as session:
            return await revise_judgment(session, run, criterion, owner, revision(reason=reason))

    replies = await asyncio.gather(
        save(NEW_REASON), save(NEW_REASON + " Other tab."), return_exceptions=True
    )
    assert sum(isinstance(r, ConflictError) for r in replies) == 1
    ack = next(r for r in replies if not isinstance(r, Exception))
    assert ack.context.assessment_revision == 2 and ack.context.current.judgment_revision == 2
    parent, rows, audits = await stored(factory, run)
    assert (
        parent.assessment_revision == 2
        and rows[0].detail["resolution"]["reason"] == ack.entry.after.reason
    )
    assert len([a for a in audits if a.event_type == "judgment_revised"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["return", "revise"])
async def test_final_decision_and_revision_reject_each_others_stale_epoch(factory, first):
    owner, run, criterion, _, _, _ = await initial(factory)
    async with factory() as session:
        if first == "return":
            await decide_report(session, run, owner, "returned", REASON, expected_revision=1)
            with pytest.raises(ConflictError):
                await revise_judgment(session, run, criterion, owner, revision())
            await reopen_report(session, run, owner, NEW_REASON, expected_revision=2)
            with pytest.raises(ConflictError):
                await revise_judgment(session, run, criterion, owner, revision())
        else:
            await revise_judgment(session, run, criterion, owner, revision())
            with pytest.raises(ConflictError):
                await decide_report(session, run, owner, "returned", REASON, expected_revision=1)


@pytest.mark.asyncio
async def test_scoring_fault_rolls_back_grade_history_and_both_epochs(factory, monkeypatch):
    import app.report.service as service

    owner, run, criterion, _, _, _ = await initial(factory)
    _, before, audits = await stored(factory, run)
    baseline = deepcopy(before[0].detail), [(a.id, deepcopy(a.payload)) for a in audits]

    async def fail(*args, **kwargs):
        raise RuntimeError("Controlled scoring fault")

    monkeypatch.setattr(service, "aggregate_and_score", fail)
    async with factory() as session:
        with pytest.raises(RuntimeError, match="Controlled scoring"):
            await revise_judgment(session, run, criterion, owner, revision())
    parent, rows, audits = await stored(factory, run)
    assert parent.assessment_revision == rows[0].judgment_revision == 1
    assert (rows[0].detail, [(a.id, a.payload) for a in audits]) == baseline
    assert rows[0].outcome == ResultOutcome.passed and rows[0].score == 100


@pytest.mark.asyncio
async def test_private_reads_reject_wrong_owner_and_changed_history_pages(factory):
    owner, run, criterion, _, _, _ = await initial(factory)
    async with factory() as session:
        with pytest.raises(NotFoundError):
            await get_judgment_context(session, run, criterion, owner + 999)
        with pytest.raises(NotFoundError):
            await get_judgment_context(session, run, criterion + 999, owner)
    async with factory() as session:
        await revise_judgment(session, run, criterion, owner, revision())
    page = await history(factory, owner, run, criterion, 2, 2, page_size=1)
    assert len(page.entries) == 1 and page.next_cursor
    earlier = await history(
        factory, owner, run, criterion, 2, 2, page_size=1, cursor=page.next_cursor
    )
    assert earlier.entries[0].action == "initial_resolution" and earlier.next_cursor is None
    async with factory() as session:
        await revise_judgment(
            session, run, criterion, owner, revision(epoch=2, token=2, resolution="mark_pass")
        )
    with pytest.raises(ConflictError):
        await history(factory, owner, run, criterion, 2, 2, cursor=page.next_cursor)
    with pytest.raises(ConflictError):
        await history(factory, owner, run, criterion, 3, 3, cursor="not a cursor")


@pytest.mark.asyncio
async def test_zero_ordinal_and_equal_point_named_grades_remain_distinct(factory):
    owner, run, ids, _ = await seed(factory)
    async with factory() as session:
        result = await session.get(CheckResult, ids[1])
        criterion = await session.get(Criterion, result.criterion_id)
        scale = deepcopy(criterion.levels)
        scale[0]["level"] = 0
        scale[1]["points"] = 0
        criterion.levels = scale
        await session.commit()
        first = await resolve_escalation_for_run(
            session, run, result.id, owner, "mark_level", REASON, level=0, expected_revision=0
        )
        ack = await revise_judgment(
            session,
            run,
            criterion.id,
            owner,
            revision(resolution="mark_level", level=2, reason=REASON),
        )
    assert first.report.results[1].level.ordinal == 0
    assert ack.entry.before.score == ack.entry.after.score == 0
    assert ack.entry.before.level.ordinal == 0 and ack.entry.after.level.ordinal == 2
    assert ack.entry.before.level.name != ack.entry.after.level.name


@pytest.mark.asyncio
async def test_wrong_producing_rubric_never_gains_revision_authority(factory):
    from app.models.rubric import Rubric

    owner, run, criterion_id, result_id, _, _ = await initial(factory)
    async with factory() as session:
        actual_run = await session.get(CheckRun, run)
        producing = await session.get(Rubric, actual_run.rubric_id)
        other = Rubric(
            instructor_id=owner,
            title="New format",
            rubric_family_id=producing.rubric_family_id,
            version=producing.version + 1,
            is_active=True,
        )
        session.add(other)
        await session.flush()
        old_criterion = await session.get(Criterion, criterion_id)
        other_criterion = Criterion(
            rubric_id=other.id,
            type=old_criterion.type,
            text="Another version's requirement",
            weight=1,
            position=0,
        )
        session.add(other_criterion)
        await session.flush()
        other_id = other_criterion.id
        await session.commit()
    async with factory() as session:
        context = await get_judgment_context(session, run, criterion_id, owner)
        assert context.criterion_text == "Explain the design"
        with pytest.raises(NotFoundError):
            await get_judgment_context(session, run, other_id, owner)
        # A malformed independent FK combination also cannot bypass binding.
        result = await session.get(CheckResult, result_id)
        result.criterion_id = other_id
        await session.commit()
        with pytest.raises(NotFoundError):
            await revise_judgment(session, run, other_id, owner, revision())
    parent, rows, _ = await stored(factory, run)
    assert parent.assessment_revision == 1 and rows[0].judgment_revision == 1


@pytest.mark.asyncio
async def test_ai_only_current_has_no_fictional_instructor_history_or_edit_token(factory):
    owner, run, ids, _ = await seed(factory)
    async with factory() as session:
        criterion = (await session.get(CheckResult, ids[0])).criterion_id
        context = await get_judgment_context(session, run, criterion, owner)
        assert context.current is None and not context.can_revise
        with pytest.raises(ConflictError, match="no available recorded"):
            await revise_judgment(session, run, criterion, owner, revision(epoch=0, token=0))
    parent, rows, audits = await stored(factory, run)
    assert parent.assessment_revision == 0 and rows[0].judgment_revision is None
    assert not [a for a in audits if a.event_type == "judgment_revised"]


@pytest.mark.asyncio
@pytest.mark.parametrize("read_kind", ["current", "history"])
async def test_private_snapshot_read_holds_parent_until_body_is_assembled(
    factory, monkeypatch, read_kind
):
    owner, run, criterion, _, _, _ = await initial(factory)
    body_ready, release = asyncio.Event(), asyncio.Event()
    async with factory() as reader, factory() as writer:
        real_commit = reader.commit

        async def gated_commit():
            body_ready.set()
            await release.wait()
            await real_commit()

        monkeypatch.setattr(reader, "commit", gated_commit)
        read = asyncio.create_task(
            get_judgment_context(reader, run, criterion, owner)
            if read_kind == "current"
            else get_judgment_history(
                reader, run, criterion, owner, expected_revision=1, expected_judgment_revision=1
            )
        )
        await asyncio.wait_for(body_ready.wait(), 3)
        writer_pid = await writer.scalar(select(func.pg_backend_pid()))
        save = asyncio.create_task(revise_judgment(writer, run, criterion, owner, revision()))

        async def wait_for_lock():
            async with factory() as observer:
                while True:
                    wait = await observer.scalar(
                        text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                        {"pid": writer_pid},
                    )
                    await observer.commit()
                    if wait == "Lock":
                        return
                    await asyncio.sleep(0.01)

        try:
            await asyncio.wait_for(wait_for_lock(), 3)
            assert not save.done()
        finally:
            release.set()
        snapshot, ack = await asyncio.gather(read, save)
    assert snapshot.assessment_revision == 1
    assert snapshot.current.judgment_revision == 1
    assert ack.context.assessment_revision == ack.context.current.judgment_revision == 2
