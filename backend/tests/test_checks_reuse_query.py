"""V-037 tests: similarity query + write-back + cold-start disclosure.
Live Postgres (own scratch DB, same convention as `test_checks_reuse_store.py`)
— this module's whole point is real pgvector queries across multiple
manuscripts, which no fake/mock can stand in for."""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.checks.reuse.query import same_instructor_hash_duplicate
from app.checks.reuse.service import (
    existing_originality_reuse_result,
    run_originality_reuse_check,
)
from app.config import get_settings
from app.db import sqlalchemy_url
from app.groups.service import DEFAULT_GROUP_LABEL, normalize_group_name
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock
from app.models.enums import ResultOutcome
from app.models.group import Group
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
                "manuscript_passage_archive, manuscript_archive, manuscript, manuscript_group, "
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


async def _seed_instructor(session_factory) -> int:
    global _seed_counter
    _seed_counter += 1
    async with session_factory() as session:
        instructor = Instructor(
            email=f"reuse-query-test-{_seed_counter}@test.local", display_name="Reuse Query Test"
        )
        session.add(instructor)
        await session.commit()
        return instructor.id


async def _make_group(session_factory, *, instructor_id: int, name: str) -> int:
    async with session_factory() as session:
        group = Group(
            instructor_id=instructor_id, name=name, name_normalized=normalize_group_name(name)
        )
        session.add(group)
        await session.commit()
        return group.id


async def _seed_manuscript_in_group(
    session_factory, *, instructor_id: int, group_id: int | None
) -> tuple[int, int]:
    async with session_factory() as session:
        manuscript = Manuscript(
            instructor_id=instructor_id,
            group_id=group_id,
            group_label="unused, group_id is what matters here",
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
        # whole document, each of its 2 chapters, AND each of its 2
        # passages (V-072/F7.4: CH1 and CH2 are each short enough to form
        # exactly one passage per chapter), all independently
        # exact-duplicate (more informative for the instructor than
        # collapsing to one flag would be).
        assert result.detail["n_flags"] == 5

        rows = (
            await session.execute(
                text(
                    "SELECT severity, detail->>'kind' AS kind, detail->>'matched_group_label' "
                    "AS matched_group FROM flag WHERE check_result_id = :id"
                ),
                {"id": result.id},
            )
        ).all()
    assert len(rows) == 5
    assert all(r.severity == "high" for r in rows)
    assert all(r.matched_group == "Group A" for r in rows)
    kinds = {r.kind for r in rows}
    assert kinds == {
        "reuse_exact_duplicate",
        "reuse_exact_duplicate_chapter",
        "reuse_exact_duplicate_passage",
    }


async def test_whole_document_and_chapter_flags_carry_real_supporting_passage_evidence(
    session_factory,
):
    """BUG-153: the flags that used to carry NULL `passage_pair` (no
    quote, no anchor, no matched text -- the ticket's own production
    evidence, flags 132/173/133/134) must now carry a real supporting
    passage, quoted from both sides, whenever the matched manuscript has
    one. Reuses the exact fixture shape as
    `test_reuploaded_duplicate_produces_a_high_severity_flag` above (a
    cross-instructor re-upload -- not a BUG-140 same-instructor
    resubmission, which collapses to a single low-severity flag and is
    tested separately below)."""
    settings = get_settings()

    manuscript_a, check_run_a = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )

    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group A (resubmission)"
    )
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session, manuscript_b, check_run_b, _extraction(CH1, CH2), settings
        )
        rows = (
            await session.execute(
                text(
                    "SELECT severity, detail->>'kind' AS kind, "
                    "detail->'supporting_passage'->>'own_text' AS supporting_own_text, "
                    "detail->'supporting_passage'->>'matched_text' AS supporting_matched_text, "
                    "detail->'supporting_passage'->>'level' AS supporting_level "
                    "FROM flag WHERE check_result_id = :id"
                ),
                {"id": result.id},
            )
        ).all()

    whole_doc_and_chapter = [r for r in rows if not r.kind.endswith("_passage")]
    assert len(whole_doc_and_chapter) == 3  # whole-doc + 2 chapters
    for row in whole_doc_and_chapter:
        assert row.severity == "high"  # evidenced -- no fallback downgrade
        # Real, checkable manuscript text on BOTH sides -- CH1/CH2 are
        # each short enough to form exactly one passage per chapter
        # (comment on the sibling test above), so the supporting passage
        # is the chapter's own heading plus its full body text (the same
        # span `_chapter_span_blocks` builds for chapter-level embedding),
        # not a fabricated placeholder.
        assert row.supporting_own_text.endswith(CH1) or row.supporting_own_text.endswith(CH2)
        assert row.supporting_matched_text.endswith(CH1) or row.supporting_matched_text.endswith(
            CH2
        )
        # Echoes the parent flag's own level (both "exact_duplicate" here
        # -- see `_supporting_passage_detail`'s own docstring for why).
        assert row.supporting_level == "exact_duplicate"


def _extraction_flat_no_chapters(body_text: str) -> ExtractionResult:
    """No detected chapter structure (`SectionTree(source="none")`) --
    `compute_document_embeddings`'s own flat-structure fallback still
    produces a real whole-document vector from every block directly, but
    `compute_passage_embeddings` produces ZERO passages for this exact
    shape (its module docstring: "No chapter structure -> no passages at
    all... an honest, named scope limit"). Exactly the one real case in
    this codebase where a genuine whole-document match can have no
    supporting passage anywhere -- used below to exercise BUG-153's own
    fallback clause."""
    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=len(body_text),
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=[_block(body_text, page=1)],
        images=[],
    )


async def test_unevidenceable_whole_document_match_is_downgraded_from_high_severity(
    session_factory,
):
    """Ticket's own fallback clause: "If a finding genuinely cannot be
    evidenced, it should not be high severity." A flat-structure
    manuscript still produces a real whole-document exact-duplicate match,
    but has no passage archive at all on either side, so no supporting
    passage can exist for it anywhere -- the flag must not stay high
    severity forcing Not Ready (BUG-150) on evidence the instructor has no
    way to check (charter judgment rule 1)."""
    settings = get_settings()

    manuscript_a, check_run_a = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session, manuscript_a, check_run_a, _extraction_flat_no_chapters(CH1), settings
        )

    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group A (resubmission)"
    )
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session, manuscript_b, check_run_b, _extraction_flat_no_chapters(CH1), settings
        )
        rows = (
            await session.execute(
                text(
                    "SELECT severity, detail->>'kind' AS kind, "
                    "detail->>'evidence_unavailable' AS evidence_unavailable, "
                    "detail->'supporting_passage' AS supporting_passage "
                    "FROM flag WHERE check_result_id = :id"
                ),
                {"id": result.id},
            )
        ).all()

    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "reuse_exact_duplicate"
    assert row.severity == "med"  # downgraded from the usual high
    assert row.evidence_unavailable == "true"
    assert row.supporting_passage is None


async def test_same_instructor_whole_document_resubmission_collapses_to_one_low_flag(
    session_factory,
):
    """BUG-140: the SAME instructor re-uploading their own manuscript must
    never reproduce `test_reuploaded_duplicate_produces_a_high_severity_flag`
    above's 5-high-severity-flag flood -- that's a resubmission question,
    not a plagiarism verdict (owner ruling, 2026-09-03). Both manuscripts
    are deliberately left ungrouped (`group_id=None`) so BUG-050's own
    group-sibling exclusion (a DIFFERENT mechanism) can't be the reason
    nothing flags here -- this proves the NEW suppression fires on its own.
    `content_hash=None` on the second call deliberately isolates the
    EMBEDDING-based resubmission path from the hash-based one (tested
    separately below)."""
    settings = get_settings()
    instructor_id = await _seed_instructor(session_factory)

    manuscript_a, check_run_a = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=None
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session,
            manuscript_a,
            check_run_a,
            _extraction(CH1, CH2),
            settings,
            instructor_id=instructor_id,
            content_hash=None,
        )

    manuscript_b, check_run_b = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=None
    )
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session,
            manuscript_b,
            check_run_b,
            _extraction(CH1, CH2),
            settings,
            instructor_id=instructor_id,
            content_hash=None,
        )
        assert result.outcome == ResultOutcome.passed
        assert result.detail["n_flags"] == 1

        rows = (
            await session.execute(
                text(
                    "SELECT severity, detail->>'kind' AS kind, detail->>'reason' AS reason "
                    "FROM flag WHERE check_result_id = :id"
                ),
                {"id": result.id},
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].severity == "low"
    assert rows[0].kind == "reuse_same_instructor_resubmission"
    assert f"#{manuscript_a}" in rows[0].reason
    assert "your own earlier upload" in rows[0].reason
    # Ground rule 3: never worded as an accusation.
    assert "plagiar" not in rows[0].reason.lower()


async def test_same_instructor_resubmission_names_the_actual_duplicate_source(
    session_factory,
):
    """The suppression is keyed on `matched_manuscript_id`, not just "any
    same-instructor exact duplicate" -- with TWO prior manuscripts on
    record (an unrelated one plus the real duplicate source), the single
    resubmission flag must still point at the actual duplicate (`manuscript_a`),
    not the unrelated one, and `n_flags` must stay at 1 rather than double-
    counting because the archive now has more than one prior entry."""
    settings = get_settings()
    instructor_id = await _seed_instructor(session_factory)

    # Own earlier upload of CH1/CH2 -- the resubmission source.
    manuscript_a, check_run_a = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=None
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session,
            manuscript_a,
            check_run_a,
            _extraction(CH1, CH2),
            settings,
            instructor_id=instructor_id,
            content_hash=None,
        )

    # A genuinely different manuscript, same instructor, unrelated content --
    # must never be pulled into the archive as a "match" by this test's own
    # construction (no third manuscript uploaded), just establishing the
    # instructor has more than one prior manuscript on record.
    other_manuscript, other_check_run = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=None
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session,
            other_manuscript,
            other_check_run,
            _extraction(
                "An entirely unrelated introduction about renewable energy policy.",
                "An entirely unrelated methodology about survey sampling.",
            ),
            settings,
            instructor_id=instructor_id,
            content_hash=None,
        )

    # Re-upload of the FIRST manuscript's content -- must resubmission-collapse
    # against manuscript_a specifically, not against the unrelated one.
    manuscript_c, check_run_c = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=None
    )
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session,
            manuscript_c,
            check_run_c,
            _extraction(CH1, CH2),
            settings,
            instructor_id=instructor_id,
            content_hash=None,
        )
        assert result.detail["n_flags"] == 1
        rows = (
            await session.execute(
                text(
                    "SELECT detail->>'matched_manuscript_id' AS ref FROM flag "
                    "WHERE check_result_id = :id"
                ),
                {"id": result.id},
            )
        ).all()
    assert len(rows) == 1
    assert int(rows[0].ref) == manuscript_a


async def test_same_instructor_hash_duplicate_query(session_factory):
    """`same_instructor_hash_duplicate` in isolation (BUG-140) -- the
    signal `run_originality_reuse_check` uses BEFORE any embedding
    comparison runs, so it must be correct on its own: matches same
    instructor + same hash, excludes self, excludes a different
    instructor's identical hash, excludes a purged manuscript, and returns
    `None` for `content_hash=None` (a manuscript ingested before this
    column existed)."""
    instructor_id = await _seed_instructor(session_factory)
    other_instructor_id = await _seed_instructor(session_factory)
    same_hash = "a" * 64

    async with session_factory() as session:
        mine = Manuscript(
            instructor_id=instructor_id,
            group_label="Group A",
            file_ref="a.pdf",
            content_hash=same_hash,
        )
        mine_no_match = Manuscript(
            instructor_id=instructor_id,
            group_label="Group A",
            file_ref="b.pdf",
            content_hash="b" * 64,
        )
        purged_twin = Manuscript(
            instructor_id=instructor_id,
            group_label="Group A",
            file_ref="c.pdf",
            content_hash=same_hash,
        )
        others_twin = Manuscript(
            instructor_id=other_instructor_id,
            group_label="Group Z",
            file_ref="d.pdf",
            content_hash=same_hash,
        )
        session.add_all([mine, mine_no_match, purged_twin, others_twin])
        await session.commit()

        purged_twin.purged_at = datetime.now(UTC)
        await session.commit()

        new_upload = Manuscript(
            instructor_id=instructor_id,
            group_label="Group A",
            file_ref="e.pdf",
            content_hash=same_hash,
        )
        session.add(new_upload)
        await session.commit()

        found = await same_instructor_hash_duplicate(
            session, new_upload.id, instructor_id, same_hash
        )
        assert found == mine.id  # lowest-id match, deterministic

        assert (
            await same_instructor_hash_duplicate(session, mine_no_match.id, instructor_id, "b" * 64)
            is None
        )
        assert (
            await same_instructor_hash_duplicate(session, new_upload.id, instructor_id, None)
            is None
        )
        # Passing the OTHER instructor's id finds THEIR own twin, never `mine`
        # -- the scoping is a real instructor filter, not just a hash match.
        assert (
            await same_instructor_hash_duplicate(
                session, new_upload.id, other_instructor_id, same_hash
            )
            == others_twin.id
        )


async def test_repeated_resubmissions_never_grow_the_archive(session_factory):
    """`backend-critic` finding (BUG-140 review): suppressing the FLAGS
    alone isn't enough -- the ticket's own fix item 1 requires a
    byte-identical re-upload to "never become an independent archive
    entry", not just to stop producing flags. If a resubmission still got
    archived, `_best_chapter_matches`/`_best_passage_match_for`'s
    independent top-1 HNSW queries (no similarity tiebreak) could resolve
    a FUTURE resubmission's per-chapter/per-passage match against a
    DIFFERENT sibling than the one whole-document match picked as the
    suppression target, leaking un-suppressed flags back in exactly the
    shape production's own 5-copy pollution produced. Three same-content
    uploads in a row must leave exactly ONE archive entry and, critically,
    resubmission #3 must still resolve deterministically to #1 (the only
    real entry) with n_flags staying at 1 -- not degrade as more
    resubmissions pile up."""
    settings = get_settings()
    instructor_id = await _seed_instructor(session_factory)

    manuscript_a, check_run_a = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=None
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session,
            manuscript_a,
            check_run_a,
            _extraction(CH1, CH2),
            settings,
            instructor_id=instructor_id,
            content_hash=None,
        )

    for _ in range(2):  # two more resubmissions of the identical content
        manuscript_n, check_run_n = await _seed_manuscript_in_group(
            session_factory, instructor_id=instructor_id, group_id=None
        )
        async with session_factory() as session:
            result = await run_originality_reuse_check(
                session,
                manuscript_n,
                check_run_n,
                _extraction(CH1, CH2),
                settings,
                instructor_id=instructor_id,
                content_hash=None,
            )
            assert result.detail["n_flags"] == 1
            rows = (
                await session.execute(
                    text(
                        "SELECT detail->>'matched_manuscript_id' AS ref FROM flag "
                        "WHERE check_result_id = :id"
                    ),
                    {"id": result.id},
                )
            ).all()
            assert len(rows) == 1
            assert int(rows[0].ref) == manuscript_a  # always the original, never a sibling

            archive_count = await session.scalar(text("SELECT count(*) FROM manuscript_archive"))
            # Only manuscript_a's own write-back -- neither resubmission
            # added a new row.
            assert archive_count == 1


async def test_first_upload_context_flagged_but_severity_untouched(session_factory):
    """BUG-097 (presentation-only remedy, owner ruling 2026-08-24): a
    brand-new instructor's very first manuscript upload is, by
    construction, a first-ever-upload match against manuscript_a's
    (different instructor's) archive. Every flag from it must carry
    `first_upload_context=True` in `detail` -- and severity must be
    UNCHANGED (still `high` for an exact duplicate) -- reversed once that
    same instructor has a second manuscript on record."""
    settings = get_settings()

    manuscript_a, check_run_a = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )

    # manuscript_b's instructor has NO other manuscript yet -- textbook
    # first upload.
    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group B"
    )
    async with session_factory() as session:
        result_b = await run_originality_reuse_check(
            session, manuscript_b, check_run_b, _extraction(CH1, CH2), settings
        )
        assert result_b.detail["first_upload_context"] is True
        rows_b = (
            await session.execute(
                text(
                    "SELECT severity, detail->>'first_upload_context' AS ctx, "
                    "detail->>'reason' AS reason FROM flag WHERE check_result_id = :id"
                ),
                {"id": result_b.id},
            )
        ).all()
    assert len(rows_b) == 5
    assert all(r.severity == "high" for r in rows_b)  # unchanged
    assert all(r.ctx == "true" for r in rows_b)
    # ux-critic finding (2026-08-24): the banner is the sole disclosure
    # surface -- `reason` must be untouched by first_upload_context, not
    # carry a duplicate caveat sentence alongside it.
    assert not any("earliest check" in r.reason.lower() for r in rows_b)

    # Same instructor (manuscript_b's), a SECOND manuscript -- no longer a
    # first upload, so no caveat and no `first_upload_context` on the flags.
    async with session_factory() as session:
        instructor_b_id = (await session.get(Manuscript, manuscript_b)).instructor_id
        rubric = Rubric(instructor_id=instructor_b_id, title="Format 2", source_file="r2.pdf")
        session.add(rubric)
        await session.commit()
        manuscript_c = Manuscript(
            instructor_id=instructor_b_id, group_label="Group B (second project)", file_ref="c.pdf"
        )
        session.add(manuscript_c)
        await session.commit()
        check_run_c = CheckRun(manuscript_id=manuscript_c.id, rubric_id=rubric.id)
        session.add(check_run_c)
        await session.commit()
        manuscript_c_id, check_run_c_id = manuscript_c.id, check_run_c.id

    async with session_factory() as session:
        result_c = await run_originality_reuse_check(
            session, manuscript_c_id, check_run_c_id, _extraction(CH1, CH2), settings
        )
        assert result_c.detail["first_upload_context"] is False
        rows_c = (
            await session.execute(
                text(
                    "SELECT detail->>'first_upload_context' AS ctx FROM flag "
                    "WHERE check_result_id = :id"
                ),
                {"id": result_c.id},
            )
        ).all()
    assert len(rows_c) == 5
    assert all(r.ctx == "false" for r in rows_c)


async def test_same_groups_own_prior_submission_is_never_reuse(session_factory):
    """BUG-050 item 1 (fixed 2026-08-19, unblocked by V-062's real
    `Group`/`group_id`): a team's own revision/re-upload must not flag
    against its own prior submission -- `exclude_manuscript_ids` used to
    exclude only the SAME ROW, so every second and later check of any
    real group reproduced five false HIGH flags."""
    settings = get_settings()
    instructor_id = await _seed_instructor(session_factory)
    group_id = await _make_group(session_factory, instructor_id=instructor_id, name="Group A")

    manuscript_a, check_run_a = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=group_id
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )

    # Same team, same group, a revised/re-uploaded manuscript row.
    manuscript_b, check_run_b = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=group_id
    )
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session, manuscript_b, check_run_b, _extraction(CH1, CH2), settings
        )
    assert result.detail["n_flags"] == 0
    assert result.detail["archive_size_n"] == 0  # the sibling doesn't even count as "archive"


async def test_different_groups_still_flag_each_other(session_factory):
    """The group exemption must not become a blanket cross-team exemption
    -- two DIFFERENT real groups (same instructor) re-using the same text
    must still flag exactly as before this fix."""
    settings = get_settings()
    instructor_id = await _seed_instructor(session_factory)
    group_a = await _make_group(session_factory, instructor_id=instructor_id, name="Group A")
    group_b = await _make_group(session_factory, instructor_id=instructor_id, name="Group B")

    manuscript_a, check_run_a = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=group_a
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )

    manuscript_b, check_run_b = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=group_b
    )
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session, manuscript_b, check_run_b, _extraction(CH1, CH2), settings
        )
    # V-072/F7.4: 2 more than before (one exact-duplicate passage flag per
    # chapter — see the reuploaded-duplicate test's own comment above).
    assert result.detail["n_flags"] == 5
    assert result.detail["archive_size_n"] == 1


async def test_ungrouped_default_bucket_is_not_exempted(session_factory):
    """Safety guard on the fix above: "Ungrouped" is the ONE shared
    fallback row every manuscript with no real team name resolves into
    per instructor (`resolve_or_create_group`) -- it is not one team, it
    is every not-yet-assigned team. Exempting its siblings would silently
    hide real reuse between two UNRELATED groups that both just haven't
    been assigned a name yet, which is the opposite of what F7 exists to
    catch. Two manuscripts sharing the "Ungrouped" bucket must still flag."""
    settings = get_settings()
    instructor_id = await _seed_instructor(session_factory)
    ungrouped_id = await _make_group(
        session_factory, instructor_id=instructor_id, name=DEFAULT_GROUP_LABEL
    )

    manuscript_a, check_run_a = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=ungrouped_id
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )

    # A genuinely different team, also defaulted to "Ungrouped".
    manuscript_b, check_run_b = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=ungrouped_id
    )
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session, manuscript_b, check_run_b, _extraction(CH1, CH2), settings
        )
    # V-072/F7.4: 2 more than before (one exact-duplicate passage flag per
    # chapter — see the reuploaded-duplicate test's own comment above).
    assert result.detail["n_flags"] == 5
    assert result.detail["archive_size_n"] == 1


def _extraction_titled(
    ch1_title: str, ch1_text: str, ch2_title: str, ch2_text: str
) -> ExtractionResult:
    blocks = [
        _block(ch1_title, page=1),
        _block(ch1_text, page=1),
        _block(ch2_title, page=5),
        _block(ch2_text, page=5),
    ]
    nodes = [
        SectionNode(title=ch1_title, level=1, page=1),
        SectionNode(title=ch2_title, level=1, page=5),
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


async def test_flag_text_never_leaks_matched_manuscripts_identity_or_headings(session_factory):
    """BUG-050/BUG-097 (Branch B display half, owner 2026-08-16): the
    corpus is shared across instructors on purpose, but a match must
    return a bounded, non-identifying reference -- never the OTHER
    instructor's real group label or the OTHER manuscript's actual
    chapter heading text (`newcomer` reproduced both leaking into a
    day-one account's very first report: the matched paper's real title
    (its `group_label`) and its real section headings, e.g. "CHAPTER 2
    REVIEW OF RELATED LITERATURE AND STUDIES"). `evidence_excerpt` is the
    one field actually rendered to the instructor (`FlagDetail.tsx`), so
    this asserts the shown text, not just `detail` (which stays internal
    and is intentionally left alone -- never serialized to any API
    response, confirmed via `app/flags/service.py`). The two manuscripts
    use DIFFERENT chapter titles over the SAME underlying content, so a
    title appearing in the flag text can only have come from the matched
    (other) side, never the querying instructor's own."""
    settings = get_settings()
    real_group_label = "BSIT-4A Attendance Monitoring System Group"
    other_ch1_title = "CHAPTER 1 THE PROBLEM AND ITS BACKGROUND"
    other_ch2_title = "CHAPTER 2 REVIEW OF RELATED LITERATURE AND STUDIES"
    own_ch1_title = "Introduction"
    own_ch2_title = "Related Work"

    manuscript_a, check_run_a = await _seed_manuscript_and_run(
        session_factory, group_label=real_group_label
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session,
            manuscript_a,
            check_run_a,
            _extraction_titled(other_ch1_title, CH1, other_ch2_title, CH2),
            settings,
        )

    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group B"
    )
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session,
            manuscript_b,
            check_run_b,
            _extraction_titled(own_ch1_title, CH1, own_ch2_title, CH2),
            settings,
        )
        rows = (
            await session.execute(
                text(
                    "SELECT evidence_excerpt, detail->>'kind' AS kind, "
                    "detail->>'reason' AS detail_reason FROM flag WHERE check_result_id = :id"
                ),
                {"id": result.id},
            )
        ).all()
    # V-072/F7.4: 2 more rows than before (one exact-duplicate passage flag
    # per chapter, CH1/CH2 are each short enough to form exactly one).
    assert len(rows) == 5
    for row in rows:
        assert real_group_label not in row.evidence_excerpt
        assert other_ch1_title not in row.evidence_excerpt
        assert other_ch2_title not in row.evidence_excerpt
        # A passage flag's evidence_excerpt is real OWN-side passage text
        # (never the matched side's, so it can't leak the other side's
        # title/heading either -- checked above), not a templated sentence
        # -- the bounded, non-identifying #ref reference lives in
        # `detail.reason` for these instead of in evidence_excerpt (see
        # `app/checks/reuse/service.py`'s wording-templates comment for
        # why). BUG-050 item 5 ("the matched manuscript must become
        # identifiable, just not identifying") still holds either way.
        if row.kind is not None and row.kind.endswith("_passage"):
            assert real_group_label not in row.detail_reason
            assert other_ch1_title not in row.detail_reason
            assert other_ch2_title not in row.detail_reason
            assert f"#{manuscript_a}" in row.detail_reason
        else:
            assert f"#{manuscript_a}" in row.evidence_excerpt


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


CH2_EXTENDED = (
    "We used a hybrid rule-based and AI approach to grading capstone manuscripts, "
    "combining deterministic checks with confidence-based escalation to the "
    "instructor. Structural criteria such as page counts, required sections, and "
    "formatting rules are verified deterministically against the parsed document "
    "tree, while semantic criteria are graded by a large language model configured "
    "with a fixed temperature and a versioned prompt so that repeated runs stay "
    "reproducible for audit purposes. Any grading result the model reports with "
    "low confidence is routed to the instructor for manual review rather than "
    "silently accepted, following the human-in-the-loop principle that governs the "
    "whole platform. The pipeline also runs four independent integrity checks in "
    "parallel: internal agreement across repeated gradings, citation integrity "
    "against external bibliographic sources, statistical forensics on any "
    "reported numeric results, and originality checking against a shared "
    "cross-account archive of previously processed manuscripts, each contributing "
    "its own evidence to the final readiness report shown to the instructor."
)
GENUINE_UNRELATED_FILLER = (
    "This chapter presents background on renewable energy adoption among small "
    "business owners in rural municipalities, framing the barriers reported in "
    "prior community surveys and outlining the scope of the present "
    "investigation. Local government units have started offering incentive "
    "programs, yet uptake remains uneven across different sectors, and very few "
    "studies document the specific obstacles that small store owners and "
    "manufacturers actually encounter when they consider installing solar "
    "panels or transitioning away from diesel generators for daily operations. "
    "The researchers conducted preliminary site visits across three "
    "municipalities, interviewing twelve business owners informally before "
    "designing the structured survey instrument used later in this study. "
    "These early conversations revealed recurring concerns about upfront cost, "
    "unreliable installation contractors, and confusion about available "
    "government subsidies, themes that recur throughout the literature "
    "reviewed in the following section and that ultimately shaped every "
    "research question this chapter goes on to state formally for the "
    "remainder of the present manuscript and its later chapters."
)


async def test_chapter_flag_supporting_passage_is_scoped_to_the_matched_chapter(session_factory):
    """`backend-critic` finding (BUG-153 review, live-reproduced): without
    chapter-index scoping, the supporting-passage lookup picked the single
    best pairing ANYWHERE in either manuscript, which could attach a
    Chapter 1 flag a passage that actually belongs to Chapter 2 -- real
    text, but self-contradicting the very finding it's shown to evidence
    the moment an instructor reads it. Reproduces that exact shape:
    manuscript B's Chapter 1 contains a genuine, unrelated ~150-word
    passage PLUS a second, separately-flushed passage verbatim-copied from
    archived manuscript A's Chapter 2 (both padded past
    `reuse_passage_chunk_words` so `_build_passages` flushes them as two
    DISTINCT passages rather than merging two short paragraphs into one --
    see `_build_passages`'s own per-block flush-on-threshold logic). The
    supporting passage attached to B's Chapter 1 flag must come from B's
    own Chapter 1 span, never leak text whose real position is Chapter 2."""
    settings = get_settings()

    manuscript_a, check_run_a = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    async with session_factory() as session:
        await run_originality_reuse_check(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2_EXTENDED), settings
        )

    unrelated_ch2 = (
        "This study evaluates the nutritional content of canteen meals served "
        "across five campuses and proposes a standardized menu review process."
    )
    blocks = [
        _block("Chapter 1: Introduction", page=1),
        _block(GENUINE_UNRELATED_FILLER, page=1),
        _block(CH2_EXTENDED, page=2),  # verbatim copy of A's Chapter 2, inside B's Chapter 1
        _block("Chapter 2: Methodology", page=5),
        _block(unrelated_ch2, page=5),
    ]
    nodes = [
        SectionNode(title="Chapter 1: Introduction", level=1, page=1),
        SectionNode(title="Chapter 2: Methodology", level=1, page=5),
    ]
    extraction_b = ExtractionResult(
        page_count=5,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        section_tree=SectionTree(source="heuristics", nodes=nodes),
        blocks=blocks,
        images=[],
    )

    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group B"
    )
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session, manuscript_b, check_run_b, extraction_b, settings
        )
        rows = (
            await session.execute(
                text(
                    "SELECT severity, detail->>'kind' AS kind, "
                    "detail->>'own_chapter_title' AS own_chapter, "
                    "detail->'supporting_passage'->>'own_text' AS supporting_own_text "
                    "FROM flag WHERE check_result_id = :id"
                ),
                {"id": result.id},
            )
        ).all()

    chapter1_flags = [r for r in rows if r.own_chapter == "Chapter 1: Introduction"]
    assert len(chapter1_flags) == 1
    flag = chapter1_flags[0]
    assert "chapter" in flag.kind
    assert flag.supporting_own_text is not None
    # The regression itself: the supporting passage's OWN text must come
    # from B's own Chapter 1 span (either the genuine filler or the
    # verbatim copy, both of which genuinely live there) -- never the
    # archived manuscript's Chapter 2 text/heading bleeding in as if it
    # were B's own words from a chapter this flag never claims.
    assert flag.supporting_own_text in (GENUINE_UNRELATED_FILLER, CH2_EXTENDED)
    assert "Chapter 2: Methodology" not in flag.supporting_own_text
    # The genuinely useful case: the copy, not the unrelated filler, is
    # what actually evidences this finding.
    assert flag.supporting_own_text == CH2_EXTENDED


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
