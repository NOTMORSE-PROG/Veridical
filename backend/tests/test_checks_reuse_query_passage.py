"""V-072 (F7.4) tests: passage-level similarity query, exclusion toggle,
self/group-sibling exclusion. Live Postgres, same scratch-DB convention as
`test_checks_reuse_query.py` (this module's own sibling) — real pgvector
queries across multiple manuscripts' passage archives."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.checks.reuse.embed import compute_passage_embeddings
from app.checks.reuse.query import query_similar_passages
from app.checks.reuse.service import run_originality_reuse_check
from app.config import get_settings
from app.db import sqlalchemy_url
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_reusepassagetest"


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
                "TRUNCATE flag, check_result, check_run, rubric, manuscript_passage_archive, "
                "manuscript_chapter_archive, manuscript_archive, manuscript, manuscript_group, "
                "group_member, instructor RESTART IDENTITY CASCADE"
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
            email=f"reuse-passage-test-{_seed_counter}@test.local",
            display_name="Reuse Passage Test",
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


BODY_TEXT = (
    "The current process for tracking attendance is entirely manual, requiring "
    "instructors to pass around a physical sheet each session. This creates delays "
    "and frequent errors in record-keeping."
)
REFERENCE_ENTRY = (
    "Reyes, J. P., & Cruz, M. A. (2023). Assessing capstone readiness in state "
    "universities. Philippine Journal of Education, 12(3), 45-67."
)


def _extraction_with_references(body_text: str, reference_text: str) -> ExtractionResult:
    blocks = [
        _block("Chapter 1: Introduction", page=1),
        _block(body_text, page=1),
        _block("References", page=5),
        _block(reference_text, page=5),
    ]
    nodes = [
        SectionNode(title="Chapter 1: Introduction", level=1, page=1),
        SectionNode(title="References", level=1, page=5),
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


async def test_reference_list_overlap_is_suppressed_by_default_and_revealed_when_toggled(
    session_factory,
):
    """QA step 2 (V-072.md): a manuscript whose only overlap is its
    reference list -> default exclusions suppress the flag; toggling
    exclusions off surfaces it with the exclusion state clearly shown."""
    settings = get_settings()

    # Manuscript A: real distinct body text, shared reference entry.
    manuscript_a, check_run_a = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session,
            manuscript_a,
            check_run_a,
            _extraction_with_references(BODY_TEXT, REFERENCE_ENTRY),
            settings,
        )

    # Manuscript B: genuinely DIFFERENT body text, but cites the SAME
    # reference entry verbatim (two unrelated teams citing the same
    # course reading -- not reuse, ticket's own edge case).
    unrelated_body = (
        "Our grading pipeline layers automated rule checks underneath an AI "
        "reviewer, escalating uncertain cases to a human instructor rather than "
        "deciding on its own, which is a completely different subject."
    )
    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group B"
    )
    extraction_b = _extraction_with_references(unrelated_body, REFERENCE_ENTRY)

    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session, manuscript_b, check_run_b, extraction_b, settings
        )
        # Default: the reference-list match never became a scored PASSAGE
        # flag (this test's own concern), AND no heading-only CHAPTER flag
        # either -- BUG-114 (fixed) used to produce a false
        # `reuse_exact_duplicate_chapter` here because a References chapter
        # whose only surviving text was its own heading word matched any
        # other manuscript's same-shaped References chapter at 1.0
        # similarity. Now asserting `n_flags == 0` outright (not just
        # passage-kind) is the BUG-114 regression guard itself -- do not
        # loosen this back to a passage-only check without re-breaking that
        # fix first.
        rows = (
            await session.execute(
                text("SELECT detail->>'kind' AS kind FROM flag WHERE check_result_id = :id"),
                {"id": result.id},
            )
        ).all()
    assert rows == []

    # Toggled off: the SAME match is real and discoverable on request,
    # clearly tagged as a reference-list match, never scored.
    async with session_factory() as session:
        passages_b = compute_passage_embeddings(extraction_b, settings)
        revealed = await query_similar_passages(
            session, manuscript_b, passages_b, settings, include_reference_list=True
        )
    assert len(revealed.matches) >= 1
    assert any(m.own_is_reference_list or m.matched_is_reference_list for m in revealed.matches)


async def test_own_group_siblings_excluded_from_passage_matching(session_factory):
    """BUG-050 item 1 applies at passage granularity too, not just
    whole-doc/chapter (query.py's own module docstring for the chapter
    case) -- a re-upload by the same team must not flag its own passages
    against its own prior submission."""
    settings = get_settings()
    from app.groups.service import normalize_group_name
    from app.models.group import Group

    async with session_factory() as session:
        instructor = Instructor(email="passage-sibling@test.local", display_name="T")
        session.add(instructor)
        await session.commit()
        group = Group(
            instructor_id=instructor.id,
            name="Group A",
            name_normalized=normalize_group_name("Group A"),
        )
        session.add(group)
        await session.commit()
        instructor_id, group_id = instructor.id, group.id

    async def _seed_in_group():
        async with session_factory() as session:
            manuscript = Manuscript(
                instructor_id=instructor_id,
                group_id=group_id,
                group_label="unused",
                file_ref="test.pdf",
            )
            session.add(manuscript)
            await session.commit()
            rubric = Rubric(instructor_id=instructor_id, title="Format", source_file="r.pdf")
            session.add(rubric)
            await session.commit()
            check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
            session.add(check_run)
            await session.commit()
            return manuscript.id, check_run.id

    manuscript_a, check_run_a = await _seed_in_group()
    extraction = _extraction_with_references(BODY_TEXT, REFERENCE_ENTRY)
    async with session_factory() as session:
        await run_originality_reuse_check(session, manuscript_a, check_run_a, extraction, settings)

    manuscript_b, check_run_b = await _seed_in_group()
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session, manuscript_b, check_run_b, extraction, settings
        )
    assert result.detail["n_flags"] == 0
    assert result.detail["passage_archive_size_n"] == 0


async def test_different_groups_same_body_text_still_flags_at_passage_level(session_factory):
    settings = get_settings()
    manuscript_a, check_run_a = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    extraction = _extraction_with_references(BODY_TEXT, REFERENCE_ENTRY)
    async with session_factory() as session:
        await run_originality_reuse_check(session, manuscript_a, check_run_a, extraction, settings)

    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group B"
    )
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session, manuscript_b, check_run_b, extraction, settings
        )
    # Body-passage exact duplicate, but the reference-list passage does
    # NOT flag (default exclusion) -- exactly one passage-level flag.
    async with session_factory() as session:
        rows = (
            await session.execute(
                text("SELECT detail->>'kind' AS kind FROM flag WHERE check_result_id = :id"),
                {"id": result.id},
            )
        ).all()
    passage_flags = [r for r in rows if r.kind is not None and r.kind.endswith("_passage")]
    assert len(passage_flags) == 1
