"""V-026 live-DB tests: flag detail, annotation, and override — the
override recomputes score/status live (ticket AC), the original AI
finding is never destroyed, and cross-instructor access is rejected.

`_seed_flagged_run` below seeds a Flag directly (no `flag.detail`,
reason lives on `check_result.detail`) rather than running a real check —
this still matches the ORIGINAL one-flag-per-check_result shape V-020's
semantic grading produces, so it doubles as regression coverage for
`_to_flag_out`'s fallback branch. F5/F6 (V-027-033, V4) now produce real
flags with their OWN per-flag `detail` (many flags share one
check_result) — see `test_flag_detail_takes_priority_over_check_result_detail`
below for that shape.
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.errors import ConflictError, NotFoundError
from app.flags.schemas import CitationSourceKeyOut
from app.flags.service import annotate_flag, confirm_citation_source, get_flag, override_flag
from app.models.citation_cache import CitationCache
from app.models.enums import CheckKind, CheckRunStatus, FlagSeverity, ReadinessStatus, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun, Flag
from app.report.service import aggregate_and_score, decide_report

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_flagstest"


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
                "TRUNCATE audit_log, citation_cache, flag, readiness_report, check_result, "
                "check_run, criterion, rubric, manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _seed_flagged_run(session):
    """A `citation_integrity` check_result (F5, not a rubric criterion —
    criterion_id NULL, same shape a real V4 check will eventually
    produce) carrying one HIGH-severity flag, plus one PASSING semantic
    criterion so the composite score is a real number a flag deduction
    can visibly move."""
    instructor = Instructor(email=f"flags-{id(session)}@test.local", display_name="Flags Test")
    session.add(instructor)
    await session.commit()
    manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
    # is_active explicitly set (not relying on the server_default) so any
    # test that ends up reading `report.rubric_is_current` (V-038) sees
    # the normal, realistic case rather than a same-session unrefreshed
    # None -- SQLAlchemy's own expire_on_commit=False gotcha, not a
    # production behavior (a fresh request-scoped session always loads
    # the real committed value from Postgres).
    rubric = Rubric(
        instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=True
    )
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

    passing_result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=criterion.id,
        kind=CheckKind.semantic,
        outcome=ResultOutcome.passed,
        score=100.0,
        detail={"basis": "llm", "verdict": "pass", "reasoning": "Clearly stated."},
    )
    flagged_result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=None,
        kind=CheckKind.citation_integrity,
        outcome=ResultOutcome.failed,
        score=None,
        detail={
            "basis": "external-api",
            "verdict": "not_supported",
            "reasoning": "Retracted source.",
        },
    )
    session.add_all([passing_result, flagged_result])
    await session.commit()
    flag = Flag(
        check_result_id=flagged_result.id,
        severity=FlagSeverity.high,
        confidence=Decimal("1.000"),
        evidence_excerpt="Wang, S. (2019). A study of things.",
        page_anchor="page 34",
    )
    session.add(flag)
    await session.commit()
    await aggregate_and_score(session, check_run.id)
    return instructor, check_run, flag


async def test_get_flag_shows_ai_finding_and_evidence(session_factory):
    async with session_factory() as session:
        instructor, _, flag = await _seed_flagged_run(session)
        out = await get_flag(session, flag.id, instructor.id)
        assert out.severity == "high"
        assert out.confidence == 1.0
        assert out.evidence_excerpt == "Wang, S. (2019). A study of things."
        assert out.ai_verdict_summary == "not_supported"
        assert out.overridden is False


async def test_annotate_saves_free_text(session_factory):
    async with session_factory() as session:
        instructor, _, flag = await _seed_flagged_run(session)
        out = await annotate_flag(session, flag.id, instructor.id, "Confirmed with the adviser.")
        assert out.annotation == "Confirmed with the adviser."


async def test_override_recomputes_score_live_and_preserves_the_original_finding(session_factory):
    async with session_factory() as session:
        instructor, check_run, flag = await _seed_flagged_run(session)

        before = await aggregate_and_score(session, check_run.id)
        # High-severity flag deduction applied — Not Ready despite a
        # perfect 100% weighted criterion score (ENGINEERING §5).
        assert before.status == ReadinessStatus.not_ready

        flag_out, check_run_id = await override_flag(
            session, flag.id, instructor.id, "Verified — this citation is not actually retracted."
        )
        assert check_run_id == check_run.id
        assert flag_out.overridden is True
        assert flag_out.override_reason == "Verified — this citation is not actually retracted."
        # Original AI finding untouched — never destroyed by the override:
        assert flag_out.evidence_excerpt == "Wang, S. (2019). A study of things."
        assert flag_out.ai_verdict_summary == "not_supported"

        after = await aggregate_and_score(session, check_run.id)
        assert after.status == ReadinessStatus.ready  # deduction gone, high-flag gate cleared
        assert after.composite_score == Decimal("100.00")


async def test_override_is_blocked_once_the_report_is_decided(session_factory):
    """V-038 / backend-critic finding: a decided report is supposed to be
    frozen (AC2) — that only means something if a flag override (which
    recomputes and PERSISTS a new composite/status onto the same
    ReadinessReport row) can't silently move the score out from under an
    already-recorded decision. Without this guard, an "approved" report
    could end up sitting on top of a score that would no longer qualify,
    with no reopen and no audit trace of the drift."""
    async with session_factory() as session:
        instructor, check_run, flag = await _seed_flagged_run(session)
        await decide_report(session, check_run.id, instructor.id, "rejected", None)

        with pytest.raises(ConflictError):
            await override_flag(session, flag.id, instructor.id, "Trying to fix it post-decision.")

        # Confirmed the override never applied — not just that it raised.
        flag_out = await get_flag(session, flag.id, instructor.id)
        assert flag_out.overridden is False


async def _seed_confirmable_citation_flag(session):
    """A real `unverifiable_not_found` citation flag (BUG-078) with a
    matching `citation_cache` row already present -- the row a check run
    would have created on its own first (uncached) lookup, which is
    always true by the time a flag referencing it exists."""
    instructor = Instructor(email=f"confirm-{id(session)}@test.local", display_name="Confirm Test")
    session.add(instructor)
    await session.commit()
    manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
    rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
    session.add_all([manuscript, rubric])
    await session.commit()
    check_run = CheckRun(
        manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
    )
    session.add(check_run)
    await session.commit()
    result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=None,
        kind=CheckKind.citation_integrity,
        outcome=ResultOutcome.passed,
        score=None,
        detail={},
    )
    session.add(result)
    await session.commit()
    cache_row = CitationCache(
        key_kind="doi",
        key_value="10.9999/local-source",
        provider="crossref",
        result={"found": False, "provider": "crossref"},
    )
    session.add(cache_row)
    await session.commit()
    flag = Flag(
        check_result_id=result.id,
        severity=FlagSeverity.low,
        evidence_excerpt="A local source not indexed by CrossRef.",
        page_anchor="reference #3",
        detail={
            "kind": "unverifiable_not_found",
            "reason": "Could not find this source in CrossRef, Semantic Scholar, "
            "Open Library, or Google Books.",
            "key_kind": "doi",
            "key_value": "10.9999/local-source",
        },
    )
    session.add(flag)
    await session.commit()
    await aggregate_and_score(session, check_run.id)
    return instructor, check_run, flag


async def test_confirm_citation_source_overrides_the_flag_and_marks_the_cache_row(session_factory):
    async with session_factory() as session:
        instructor, check_run, flag = await _seed_confirmable_citation_flag(session)

        pre_out = await get_flag(session, flag.id, instructor.id)
        assert pre_out.citation_source_key == CitationSourceKeyOut(
            kind="doi", value="10.9999/local-source"
        )
        assert pre_out.confirmed_citation_source is False

        flag_out, check_run_id = await confirm_citation_source(
            session, flag.id, instructor.id, "Verified on the publisher's own website."
        )
        assert check_run_id == check_run.id
        assert flag_out.overridden is True
        assert flag_out.override_reason == "Verified on the publisher's own website."
        assert flag_out.confirmed_citation_source is True
        # Original AI finding untouched -- never destroyed, same convention as override_flag.
        assert flag_out.evidence_excerpt == "A local source not indexed by CrossRef."

        row = await session.scalar(
            select(CitationCache).where(
                CitationCache.key_kind == "doi", CitationCache.key_value == "10.9999/local-source"
            )
        )
        assert row.instructor_confirmed is True
        assert row.instructor_confirmed_at is not None


async def test_confirm_citation_source_rejects_a_flag_with_nothing_to_confirm(session_factory):
    async with session_factory() as session:
        instructor, _, flag = await _seed_flagged_run(session)  # ordinary retraction-shaped flag
        with pytest.raises(ConflictError):
            await confirm_citation_source(session, flag.id, instructor.id, "I checked it.")
        # Confirmed it never applied -- not just that it raised.
        flag_out = await get_flag(session, flag.id, instructor.id)
        assert flag_out.overridden is False
        assert flag_out.confirmed_citation_source is False


async def test_confirm_citation_source_is_blocked_once_the_report_is_decided(session_factory):
    async with session_factory() as session:
        instructor, check_run, flag = await _seed_confirmable_citation_flag(session)
        await decide_report(
            session,
            check_run.id,
            instructor.id,
            "rejected",
            "Deliberately disagreeing for this test.",
        )
        with pytest.raises(ConflictError):
            await confirm_citation_source(session, flag.id, instructor.id, "I checked it.")


async def _seed_two_flags_one_check_result(session):
    """Mirrors the REAL F5/F6 shape (V-027-033): many `Flag` rows under ONE
    `check_result`, each with its own `detail` — the shape
    `check_result.detail`'s single top-level key can't represent (V-033's
    reason for adding `Flag.detail`). Proves two flags sharing a
    check_result get independent reasons rather than the check_result's
    one shared `detail` bleeding into both."""
    instructor = Instructor(email=f"flags3-{id(session)}@test.local", display_name="Flags Test 3")
    session.add(instructor)
    await session.commit()
    manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.docx")
    rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
    session.add_all([manuscript, rubric])
    await session.commit()
    check_run = CheckRun(
        manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
    )
    session.add(check_run)
    await session.commit()

    # check_result.detail carries only aggregate counts (V-033's real
    # shape) — never a per-flag reason — so a stale fallback to it would
    # surface the WRONG text for both flags below.
    result = CheckResult(
        check_run_id=check_run.id,
        criterion_id=None,
        kind=CheckKind.statistical_forensics,
        outcome=ResultOutcome.passed,
        detail={"n_inferential_stats": 1, "n_descriptive_stats": 1, "n_flags": 2},
    )
    session.add(result)
    await session.commit()
    grim_flag = Flag(
        check_result_id=result.id,
        severity=FlagSeverity.high,
        evidence_excerpt="n=10, M=3.33",
        page_anchor="page 12",
        detail={"kind": "grim_inconsistent", "reason": "Reported mean is not a possible value."},
    )
    pvalue_flag = Flag(
        check_result_id=result.id,
        severity=FlagSeverity.med,
        evidence_excerpt="t(28)=2.45, p=.500",
        page_anchor="page 14",
        detail={"kind": "p_value_mismatch", "reason": "Recomputed p-value does not match."},
    )
    session.add_all([grim_flag, pvalue_flag])
    await session.commit()
    return instructor, grim_flag, pvalue_flag


async def test_flag_detail_takes_priority_over_check_result_detail(session_factory):
    async with session_factory() as session:
        instructor, grim_flag, pvalue_flag = await _seed_two_flags_one_check_result(session)

        grim_out = await get_flag(session, grim_flag.id, instructor.id)
        # BUG-053 fix: this detail shape has no "verdict"/"basis" key (F6's
        # own vocabulary is "kind"/"reason", V-033) -- it used to read as
        # `None` ("AI verdict: unavailable") even though a real finding
        # exists; `flag_ai_verdict_summary` now falls back to "kind".
        assert grim_out.ai_verdict_summary == "grim_inconsistent"
        assert grim_out.ai_reasoning == "Reported mean is not a possible value."

        pvalue_out = await get_flag(session, pvalue_flag.id, instructor.id)
        assert pvalue_out.ai_reasoning == "Recomputed p-value does not match."

        # Two flags, one check_result, two DISTINCT reasons — proves
        # `flag.detail` (not `check_result.detail`) is the source.
        assert grim_out.ai_reasoning != pvalue_out.ai_reasoning


async def test_ai_reasoning_is_suppressed_when_identical_to_evidence_excerpt(session_factory):
    """BUG-112: F7 reuse flags set `detail["reason"]` to the exact same
    sentence as `evidence_excerpt` -- `ai_reasoning` must not fall back to
    it in that case, or the frontend renders one sentence twice. A
    genuinely distinct `detail["reason"]` (the pre-existing F5/F6 shape,
    `test_flag_detail_takes_priority_over_check_result_detail` above) must
    still come through unsuppressed."""
    async with session_factory() as session:
        instructor = Instructor(email=f"dupreason-{id(session)}@test.local", display_name="T")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()
        check_run = CheckRun(
            manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
        )
        session.add(check_run)
        await session.commit()
        result = CheckResult(
            check_run_id=check_run.id,
            criterion_id=None,
            kind=CheckKind.originality_reuse,
            outcome=ResultOutcome.failed,
            score=None,
            detail={},
        )
        session.add(result)
        await session.commit()
        same_sentence = "Possible reuse of a passage found elsewhere in VERIDICAL's library."
        duplicated_flag = Flag(
            check_result_id=result.id,
            severity=FlagSeverity.high,
            evidence_excerpt=same_sentence,
            page_anchor="page 3",
            detail={"kind": "reuse_passage_exact_duplicate", "reason": same_sentence},
        )
        distinct_flag = Flag(
            check_result_id=result.id,
            severity=FlagSeverity.med,
            evidence_excerpt="The manuscript claims X.",
            page_anchor="page 4",
            detail={"kind": "agreement_contradictory", "reason": "This appears to contradict Y."},
        )
        session.add_all([duplicated_flag, distinct_flag])
        await session.commit()

        duplicated_out = await get_flag(session, duplicated_flag.id, instructor.id)
        assert duplicated_out.evidence_excerpt == same_sentence
        assert duplicated_out.ai_reasoning is None

        distinct_out = await get_flag(session, distinct_flag.id, instructor.id)
        assert distinct_out.ai_reasoning == "This appears to contradict Y."


async def test_evidence_unavailable_flag_reaches_the_api_distinguishably(session_factory):
    """BUG-153 (`backend-critic` finding, live-reproduced): `checks.reuse.
    service` downgrades an unevidenceable whole-doc/chapter flag from high
    to med severity, but its WORDING stays the same templated accusation
    sentence either way (there is nothing else honest to say) -- without
    `evidence_unavailable` reaching `FlagOut`, this looked identical to a
    genuinely medium-confidence match, both in the API response and on
    every screen. Seeded directly (same convention as the fixtures above)
    rather than through the full reuse pipeline -- this is specifically
    about `_to_flag_out`'s wiring, not about how the upstream check
    decided to set the key."""
    async with session_factory() as session:
        instructor = Instructor(email=f"evidenceunavail-{id(session)}@test.local", display_name="T")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()
        check_run = CheckRun(
            manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
        )
        session.add(check_run)
        await session.commit()
        result = CheckResult(
            check_run_id=check_run.id,
            criterion_id=None,
            kind=CheckKind.originality_reuse,
            outcome=ResultOutcome.passed,
            detail={},
        )
        session.add(result)
        await session.commit()
        downgraded_flag = Flag(
            check_result_id=result.id,
            severity=FlagSeverity.med,  # already downgraded from high by the caller
            evidence_excerpt="This manuscript appears to be an exact or near-exact textual "
            "duplicate of archived manuscript #9: possible resubmission or reuse. "
            "Please verify manually.",
            page_anchor="whole document",
            detail={"kind": "reuse_exact_duplicate", "evidence_unavailable": True},
        )
        genuinely_medium_flag = Flag(
            check_result_id=result.id,
            severity=FlagSeverity.med,
            evidence_excerpt="This manuscript shows high textual similarity to archived "
            "manuscript #4: possible shared content or reuse. Please verify manually.",
            page_anchor="whole document",
            detail={"kind": "reuse_high_similarity"},
        )
        session.add_all([downgraded_flag, genuinely_medium_flag])
        await session.commit()

        downgraded_out = await get_flag(session, downgraded_flag.id, instructor.id)
        assert downgraded_out.severity == "med"
        assert downgraded_out.evidence_unavailable is True
        assert downgraded_out.passage_pair is None

        medium_out = await get_flag(session, genuinely_medium_flag.id, instructor.id)
        assert medium_out.severity == "med"
        assert medium_out.evidence_unavailable is False


async def test_no_verdict_high_flag_does_not_force_not_ready_live(session_factory):
    """BUG-053 Option A, end to end: a high-severity flag whose underlying
    check left no real finding behind (no "kind"/"reason"/"verdict"/
    "basis" anywhere) must not decide the report by default. Not
    reachable by any shipped check today (each always sets "kind" at
    minimum) — seeded directly to prove the scoring engine's own side of
    the guarantee, the same way `_seed_two_flags_one_check_result` proves
    the per-flag-detail shape without running a real check."""
    async with session_factory() as session:
        instructor = Instructor(email=f"noverdict-{id(session)}@test.local", display_name="T")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()
        criterion = Criterion(
            rubric_id=rubric.id, type="structural", text="C", weight=Decimal("100"), position=0
        )
        session.add(criterion)
        await session.commit()
        check_run = CheckRun(
            manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done
        )
        session.add(check_run)
        await session.commit()
        passing_result = CheckResult(
            check_run_id=check_run.id,
            criterion_id=criterion.id,
            kind=CheckKind.structural,
            outcome=ResultOutcome.passed,
            score=100.0,
            detail={"basis": "rule"},
        )
        empty_result = CheckResult(
            check_run_id=check_run.id,
            criterion_id=None,
            kind=CheckKind.originality_reuse,
            outcome=ResultOutcome.passed,
            score=None,
            detail={},
        )
        session.add_all([passing_result, empty_result])
        await session.commit()
        no_verdict_flag = Flag(
            check_result_id=empty_result.id,
            severity=FlagSeverity.high,
            evidence_excerpt="x",
            page_anchor="page 1",
            detail={},  # no "kind"/"reason" -- the genuinely-unreachable case
        )
        session.add(no_verdict_flag)
        await session.commit()

        report = await aggregate_and_score(session, check_run.id)
        assert report.status == ReadinessStatus.ready  # not forced not_ready by the empty flag
        assert report.composite_score == Decimal("100.00")


async def test_cross_instructor_access_is_rejected_not_leaked(session_factory):
    async with session_factory() as session:
        _, _, flag = await _seed_flagged_run(session)
        stranger = Instructor(email="stranger@test.local", display_name="Stranger")
        session.add(stranger)
        await session.commit()

        with pytest.raises(NotFoundError):
            await get_flag(session, flag.id, stranger.id)
        with pytest.raises(NotFoundError):
            await override_flag(session, flag.id, stranger.id, "reason")
