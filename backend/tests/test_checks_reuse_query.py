"""V-037 tests: similarity query + write-back + cold-start disclosure.
Live Postgres (own scratch DB, same convention as `test_checks_reuse_store.py`)
— this module's whole point is real pgvector queries across multiple
manuscripts, which no fake/mock can stand in for."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.checks.reuse.service import (
    existing_originality_reuse_result,
    run_originality_reuse_check,
)
from app.config import get_settings
from app.db import sqlalchemy_url
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock
from app.models.enums import ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_reusequerytest"


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
    engine = create_async_engine(sqlalchemy_url(scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean(session_factory):
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE flag, check_result, check_run, rubric, manuscript_chapter_archive, "
                "manuscript_archive, manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


_seed_counter = 0


async def _seed_manuscript_and_run(session_factory, *, group_label: str) -> tuple[int, int]:
    global _seed_counter
    _seed_counter += 1
    async with session_factory() as session:
        instructor = Instructor(
            email=f"reuse-query-test-{_seed_counter}@test.local", display_name="Reuse Query Test"
        )
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(
            instructor_id=instructor.id, group_label=group_label, file_ref="test.pdf"
        )
        session.add(manuscript)
        await session.commit()
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add(rubric)
        await session.commit()
        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        await session.commit()
        return manuscript.id, check_run.id


def _block(text_: str, *, page: int = 1) -> TextBlock:
    return TextBlock(text=text_, page=page, max_font_size=11.0, bold_ratio=0.0)


def _extraction(chapter1_text: str, chapter2_text: str) -> ExtractionResult:
    blocks = [
        _block("Chapter 1: Introduction", page=1),
        _block(chapter1_text, page=1),
        _block("Chapter 2: Methodology", page=5),
        _block(chapter2_text, page=5),
    ]
    nodes = [
        SectionNode(title="Chapter 1: Introduction", level=1, page=1),
        SectionNode(title="Chapter 2: Methodology", level=1, page=5),
    ]
    return ExtractionResult(
        page_count=5,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        section_tree=SectionTree(source="heuristics", nodes=nodes),
        blocks=blocks,
        images=[],
    )


CH1 = (
    "The current process for tracking attendance is entirely manual, requiring "
    "instructors to pass around a physical sheet each session. This creates delays "
    "and frequent errors in record-keeping."
)
CH2 = (
    "We used a hybrid rule-based and AI approach to grading capstone manuscripts, "
    "combining deterministic checks with confidence-based escalation to the "
    "instructor."
)


async def test_first_manuscript_ever_is_cold_start_honest(session_factory):
    manuscript_id, check_run_id = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    settings = get_settings()
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session, manuscript_id, check_run_id, _extraction(CH1, CH2), settings
        )
    assert result.outcome == ResultOutcome.passed
    assert result.detail["archive_size_n"] == 0
    assert result.detail["n_flags"] == 0


async def test_reuploaded_duplicate_produces_a_high_severity_flag(session_factory):
    """Ticket AC (V5 demo seed): re-uploading the same manuscript
    (renamed) -> exact-dup flag."""
    settings = get_settings()

    # First upload.
    manuscript_a, check_run_a = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )

    # Same content, "renamed" (different group_label, different manuscript row).
    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group A (resubmission)"
    )
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session, manuscript_b, check_run_b, _extraction(CH1, CH2), settings
        )
        assert result.detail["archive_size_n"] == 1  # compared against the first upload
        # A true duplicate flags at EVERY granularity that matches — the
        # whole document AND each of its 2 chapters, all independently
        # exact-duplicate (more informative for the instructor than
        # collapsing to one flag would be).
        assert result.detail["n_flags"] == 3

        rows = (
            await session.execute(
                text(
                    "SELECT severity, detail->>'kind' AS kind, detail->>'matched_group_label' "
                    "AS matched_group FROM flag WHERE check_result_id = :id"
                ),
                {"id": result.id},
            )
        ).all()
    assert len(rows) == 3
    assert all(r.severity == "high" for r in rows)
    assert all(r.matched_group == "Group A" for r in rows)
    kinds = {r.kind for r in rows}
    assert kinds == {"reuse_exact_duplicate", "reuse_exact_duplicate_chapter"}


async def test_transplanted_chapter_produces_a_chapter_level_flag(session_factory):
    """Ticket AC (V5 demo seed): a chapter transplanted into a new doc ->
    chapter-level flag, even when the WHOLE document is otherwise
    different."""
    settings = get_settings()
    unrelated_ch2 = (
        "This study evaluates the nutritional content of canteen meals served "
        "across five campuses and proposes a standardized menu review process."
    )

    manuscript_a, check_run_a = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )

    # Manuscript B: chapter 1 is CH1 verbatim (transplanted), chapter 2 is
    # genuinely unrelated content — the WHOLE-doc vector should be diluted
    # enough to miss the whole-doc threshold, but the chapter-level query
    # must still catch the transplanted chapter specifically.
    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group B"
    )
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session, manuscript_b, check_run_b, _extraction(CH1, unrelated_ch2), settings
        )
        rows = (
            await session.execute(
                text(
                    "SELECT severity, detail->>'kind' AS kind, "
                    "detail->>'own_chapter_title' AS own_chapter "
                    "FROM flag WHERE check_result_id = :id"
                ),
                {"id": result.id},
            )
        ).all()
    chapter_flags = [r for r in rows if r.own_chapter is not None]
    assert len(chapter_flags) == 1
    assert chapter_flags[0].own_chapter == "Chapter 1: Introduction"
    assert "chapter" in chapter_flags[0].kind


async def test_same_topic_different_wording_does_not_flag(session_factory):
    """Ticket QA step: same-topic-but-original pairs must NOT flag (false
    accusation guard, precision > recall)."""
    settings = get_settings()
    same_topic_different_wording = (
        "Attendance monitoring in most classrooms today still relies on paper "
        "sign-in sheets, which instructors must manually tally afterward, a "
        "process prone to mistakes and wasted class time."
    )
    different_ch2 = (
        "Our grading pipeline layers automated rule checks underneath an AI "
        "reviewer, escalating uncertain cases to a human instructor rather than "
        "deciding on its own."
    )

    manuscript_a, check_run_a = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )

    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group C"
    )
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session,
            manuscript_b,
            check_run_b,
            _extraction(same_topic_different_wording, different_ch2),
            settings,
        )
    assert result.detail["n_flags"] == 0


async def test_write_back_happens_after_check_never_matches_self(session_factory):
    """Ticket AC (F7.3): write-back after the check completes — the
    manuscript's own embedding must never appear as a match against
    itself, and the archive gains exactly one new row per check."""
    settings = get_settings()
    manuscript_id, check_run_id = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session, manuscript_id, check_run_id, _extraction(CH1, CH2), settings
        )
    assert result.detail["n_flags"] == 0  # never matched itself

    async with session_factory() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM manuscript_archive WHERE manuscript_id = :id"),
            {"id": manuscript_id},
        )
    assert count == 1


async def test_existing_result_guard_finds_the_completed_run(session_factory):
    """ENGINEERING §4 contract: the pipeline checks this guard BEFORE
    calling `run_originality_reuse_check` again on a resumed check_run —
    same convention as every other integrity check's `existing_*_result`."""
    manuscript_id, check_run_id = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    settings = get_settings()
    extraction = _extraction(CH1, CH2)
    async with session_factory() as session:
        first = await run_originality_reuse_check(
            session, manuscript_id, check_run_id, extraction, settings
        )
        existing = await existing_originality_reuse_result(session, check_run_id)
    assert existing is not None
    assert existing.id == first.id

    async with session_factory() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM manuscript_archive WHERE manuscript_id = :id"),
            {"id": manuscript_id},
        )
    assert count == 1  # a naive re-run through the service would double-write
