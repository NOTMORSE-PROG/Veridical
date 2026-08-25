"""V-069 live-DB tests: levelled-rubric representation end to end — a
criterion carrying its own scale, escalation resolution with `mark_level`,
the `mark_pass`/`mark_fail`-on-a-levelled-criterion guard, and the
report's `levelled_rating` transcription. Same scratch-DB convention as
`test_report_escalation_live.py`.
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.errors import ConflictError
from app.models.enums import CheckKind, CheckRunStatus, CriterionType, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun
from app.report.service import (
    aggregate_and_score,
    get_report,
    list_escalated_for_run,
    resolve_escalation_for_run,
)

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_v069test"

TIP_SCALE = [
    {"level": 1, "name": "Beginner", "descriptor": "no clear structure", "points": 1},
    {"level": 2, "name": "Acceptable", "descriptor": "states the topic", "points": 2},
    {"level": 3, "name": "Proficient", "descriptor": "states and previews", "points": 3},
    {"level": 4, "name": "Exemplary", "descriptor": "engaging and complete", "points": 4},
]


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


async def _seed_levelled_run(session, *, ai_majority_verdict="Proficient", agreement=0.667):
    """One check_run with ONE levelled criterion sitting at `escalated`
    (a disagreeing vote pending instructor review) and one ordinary
    pass/fail criterion already decided -- proves AC3 (pass/fail
    unaffected) and AC4 (level-scoped resolution) inside the same run."""
    instructor = Instructor(email=f"v069-{id(session)}@test.local", display_name="V069 Test")
    session.add(instructor)
    await session.commit()
    manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
    rubric = Rubric(instructor_id=instructor.id, title="TIP-VPAA-054B", source_file="r.pdf")
    session.add_all([manuscript, rubric])
    await session.commit()
    levelled_criterion = Criterion(
        rubric_id=rubric.id,
        type=CriterionType.semantic,
        text="Introduction clearly states the topic and previews structure",
        evidence=None,
        weight=Decimal("50"),
        position=0,
        levels=TIP_SCALE,
    )
    plain_criterion = Criterion(
        rubric_id=rubric.id,
        type=CriterionType.structural,
        text="Bibliography has at least 5 references",
        evidence=None,
        weight=Decimal("50"),
        position=1,
        levels=None,
    )
    session.add_all([levelled_criterion, plain_criterion])
    await session.commit()
    check_run = CheckRun(
        manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
    )
    session.add(check_run)
    await session.commit()

    detail = {
        "basis": "llm",
        "agreement": agreement,
        "votes": ["Proficient", "Acceptable", ai_majority_verdict]
        if ai_majority_verdict
        else ["Acceptable", "Proficient"],
        "context_label": "INTRODUCTION",
        "prompt_version": "v3",
    }
    if ai_majority_verdict is not None:
        detail["verdict"] = ai_majority_verdict
        detail["reasoning"] = "Two of three passes agreed."
        detail["evidence"] = [{"quote": "The introduction states the topic.", "anchor": "page 1"}]
    escalated_result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=levelled_criterion.id,
        kind=CheckKind.semantic,
        outcome=ResultOutcome.escalated,
        score=None,
        detail=detail,
    )
    decided_plain_result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=plain_criterion.id,
        kind=CheckKind.structural,
        outcome=ResultOutcome.passed,
        score=Decimal("100.0"),
        detail={"basis": "rule", "reason": "5 references found."},
    )
    session.add_all([escalated_result, decided_plain_result])
    await session.commit()
    await aggregate_and_score(session, check_run.id)
    return instructor, check_run, escalated_result, levelled_criterion, plain_criterion


async def test_escalated_levelled_item_carries_its_own_scale_to_the_panel(session_factory):
    async with session_factory() as session:
        instructor, check_run, _, _, _ = await _seed_levelled_run(session)
        panel = await list_escalated_for_run(session, check_run.id, instructor.id)
        assert len(panel) == 1
        assert panel[0].levels is not None
        assert [lvl.name for lvl in panel[0].levels] == [
            "Beginner",
            "Acceptable",
            "Proficient",
            "Exemplary",
        ]


async def test_mark_level_resolves_to_the_chosen_rung_and_updates_score_live(session_factory):
    async with session_factory() as session:
        instructor, check_run, escalated_result, _, _ = await _seed_levelled_run(session)
        out = await resolve_escalation_for_run(
            session,
            check_run.id,
            escalated_result.id,
            instructor.id,
            "mark_level",
            "Read the introduction myself -- it previews the structure but isn't engaging.",
            level=3,  # Proficient
        )
        assert out.outcome == "passed"
        assert out.score == 75.0  # 3/4 * 100
        # Both criteria now decided (50% Proficient=75, 50% plain pass=100)
        # -> composite 87.5, byte-verifiable arithmetic.
        assert out.report.composite_score == 87.5
        levelled_row = next(r for r in out.report.results if r.level is not None)
        assert levelled_row.level.name == "Proficient"
        assert levelled_row.level.points == 3.0

        panel = await list_escalated_for_run(session, check_run.id, instructor.id)
        assert panel == []


async def test_mark_level_with_an_unknown_ordinal_is_rejected(session_factory):
    async with session_factory() as session:
        instructor, check_run, escalated_result, _, _ = await _seed_levelled_run(session)
        with pytest.raises(ConflictError):
            await resolve_escalation_for_run(
                session,
                check_run.id,
                escalated_result.id,
                instructor.id,
                "mark_level",
                "Trying an ordinal this scale doesn't have.",
                level=99,
            )


async def test_mark_pass_on_a_levelled_criterion_is_rejected(session_factory):
    """AC4: 'on a 1-4 rubric they choose a level, not Pass/Fail' -- the
    resolution vocabulary itself must match the rubric's own scale."""
    async with session_factory() as session:
        instructor, check_run, escalated_result, _, _ = await _seed_levelled_run(session)
        with pytest.raises(ConflictError):
            await resolve_escalation_for_run(
                session,
                check_run.id,
                escalated_result.id,
                instructor.id,
                "mark_pass",
                "Trying to force a binary decision onto a graded scale.",
            )


async def test_mark_level_on_a_non_levelled_criterion_is_rejected(session_factory):
    async with session_factory() as session:
        instructor, check_run, _, _, plain_criterion = await _seed_levelled_run(session)
        # Force the plain criterion into an escalated state to resolve against.
        plain_result = (
            await session.execute(
                select(CheckResult).where(CheckResult.criterion_id == plain_criterion.id)
            )
        ).scalar_one()
        plain_result.outcome = ResultOutcome.escalated
        plain_result.score = None
        await session.commit()
        with pytest.raises(ConflictError):
            await resolve_escalation_for_run(
                session,
                check_run.id,
                plain_result.id,
                instructor.id,
                "mark_level",
                "This criterion has no scale to pick a level from.",
                level=2,
            )


async def test_accept_majority_on_a_levelled_criterion_uses_its_own_scale(session_factory):
    async with session_factory() as session:
        instructor, check_run, escalated_result, _, _ = await _seed_levelled_run(
            session, ai_majority_verdict="Exemplary"
        )
        out = await resolve_escalation_for_run(
            session,
            check_run.id,
            escalated_result.id,
            instructor.id,
            "accept_majority",
            "Agreed with the AI's own lean after reading the excerpt myself.",
        )
        assert out.outcome == "passed"
        assert out.score == 100.0  # 4/4 * 100


async def test_report_shows_the_rubrics_own_levelled_rating(session_factory):
    async with session_factory() as session:
        instructor, check_run, escalated_result, _, _ = await _seed_levelled_run(session)
        await resolve_escalation_for_run(
            session,
            check_run.id,
            escalated_result.id,
            instructor.id,
            "mark_level",
            "Read it myself -- Proficient, previews structure but not engaging.",
            level=3,
        )
        report = await get_report(session, check_run.id, instructor.id)
        # V-069 AC2: only the ONE levelled criterion in this rubric counts
        # toward the RATING formula -- the plain structural criterion is
        # excluded from it entirely (it has no points scale), even though
        # it DOES count toward the ordinary weighted composite above.
        assert report.levelled_rating is not None
        assert report.levelled_rating.achieved_points == 3.0
        assert report.levelled_rating.max_points == 4.0
        assert report.levelled_rating.rating_percent == 75.0
        assert report.levelled_rating.n_decided == 1
        assert report.levelled_rating.n_levelled == 1


async def test_a_pass_fail_only_rubric_report_has_no_levelled_rating_live(session_factory):
    """AC3, live-DB proof: a rubric with zero levelled criteria produces a
    report with `levelled_rating is None` -- never a new visible field for
    the common case."""
    async with session_factory() as session:
        instructor = Instructor(email="plain@test.local", display_name="Plain")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Plain format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()
        criterion = Criterion(
            rubric_id=rubric.id,
            type=CriterionType.structural,
            text="Has an abstract",
            evidence=None,
            weight=Decimal("100"),
            position=0,
            levels=None,
        )
        session.add(criterion)
        await session.commit()
        check_run = CheckRun(
            manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
        )
        session.add(check_run)
        await session.commit()
        session.add(
            CheckResult(
                check_run_id=check_run.id,
                criterion_id=criterion.id,
                kind=CheckKind.structural,
                outcome=ResultOutcome.passed,
                score=Decimal("100.0"),
                detail={"basis": "rule"},
            )
        )
        await session.commit()
        await aggregate_and_score(session, check_run.id)
        report = await get_report(session, check_run.id, instructor.id)
        assert report.levelled_rating is None
        assert report.results[0].level is None
