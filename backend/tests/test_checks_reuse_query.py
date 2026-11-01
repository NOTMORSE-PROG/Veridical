"""V-037 tests: similarity query + cold-start disclosure. Since V-077
(D-028) the corpus is the Library: tests seed it with `_add_to_library`, and
a check never writes back.
Live Postgres (own scratch DB, same convention as `test_checks_reuse_store.py`)
— this module's whole point is real pgvector queries across multiple
manuscripts, which no fake/mock can stand in for."""

import json
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.checks.reuse.embed import compute_document_embeddings, compute_passage_embeddings
from app.checks.reuse.query import same_instructor_hash_duplicate
from app.checks.reuse.service import (
    existing_originality_reuse_result,
    run_originality_reuse_check,
)
from app.checks.reuse.store import store_document_embeddings, store_passage_embeddings
from app.config import get_settings
from app.db import sqlalchemy_url
from app.findings.policy import exclusion_reason
from app.groups.service import DEFAULT_GROUP_LABEL, normalize_group_name
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock
from app.ingest.service import _write_raw_store, raw_store_path
from app.models.enums import LibraryEntrySource, LibraryEntryStatus, ResultOutcome
from app.models.group import Group, GroupMember
from app.models.instructor import Instructor
from app.models.library import LibraryEntry
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_reusequerytest"


@pytest.fixture(autouse=True)
def _private_source_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "comparison-data"))
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
                "TRUNCATE library_entry, audit_log, flag, check_result, check_run, "
                "manuscript_chapter_archive, manuscript_passage_archive, manuscript_archive, "
                "manuscript, manuscript_group, "
                "group_member, rubric, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


_seed_counter = 0


async def _run_with_saved_source(session, manuscript_id, run_id, extraction, settings, **kwargs):
    # These vector controls enter below ingestion. Persist exactly the checked
    # input as ingestion would, without mocking source admission or retrieval.
    _write_raw_store(extraction, raw_store_path(settings, manuscript_id))
    return await run_originality_reuse_check(
        session, manuscript_id, run_id, extraction, settings, **kwargs
    )


async def test_scoped_weak_support_keeps_text_without_inventing_a_passage_band(session_factory):
    """Real pgvector search retains weaker support without making a standalone lead."""
    import math

    from sqlalchemy import delete, select

    from app.checks.reuse.embed import PassageEmbedding
    from app.checks.reuse.query import _best_passage_match_for, best_supporting_passage
    from app.checks.reuse.service import _supporting_passage_detail
    from app.flags.service import _passage_pair_from_detail
    from app.models.enums import CheckKind
    from app.models.manuscript import ManuscriptPassageArchive
    from app.models.run import CheckResult

    settings = get_settings().model_copy(
        update={
            "reuse_passage_high_similarity_threshold": 0.75,
            "reuse_passage_exact_duplicate_threshold": 0.95,
        }
    )
    target, target_run = await _seed_manuscript_and_run(
        session_factory, group_label="Scoped target"
    )
    rival, _ = await _seed_manuscript_and_run(session_factory, group_label="Wrong manuscript")
    # This control constructs vectors directly, so persist the corresponding
    # synthetic PDF extraction required by selected-source admission too.
    _write_raw_store(
        ExtractionResult(
            page_count=4,
            anchor_kind="page",
            image_only=False,
            text_chars=len("Before stored. Stored central excerpt After stored."),
            blocks=[
                TextBlock(
                    page=4,
                    text="Before stored. Stored central excerpt After stored.",
                    max_font_size=11,
                    bold_ratio=0,
                )
            ],
            images=[],
            section_tree=SectionTree(source="none", nodes=[]),
        ),
        raw_store_path(settings, target),
    )
    own_vector = [1.0] + [0.0] * (settings.embedding_dim - 1)
    weak_vector = [0.2, math.sqrt(1 - 0.2**2)] + [0.0] * (settings.embedding_dim - 2)
    own = PassageEmbedding(
        passage_index=0,
        chapter_index=1,
        anchor_kind="page",
        page=3,
        paragraph=None,
        char_start=0,
        char_end=19,
        text="Own central excerpt",
        context_text="Before own. Own central excerpt After own.",
        is_reference_list=False,
        is_block_quote=False,
        embedding=own_vector,
    )
    async with session_factory() as session:
        for manuscript_id, chapter, index, vector in (
            (target, 2, 0, weak_vector),
            (target, 3, 1, own_vector),
            (rival, 2, 0, own_vector),
        ):
            session.add(
                ManuscriptPassageArchive(
                    manuscript_id=manuscript_id,
                    passage_index=index,
                    chapter_index=chapter,
                    page=4,
                    paragraph=None,
                    char_start=0,
                    char_end=22,
                    text="Stored central excerpt",
                    context_text="Before stored. Stored central excerpt After stored.",
                    is_reference_list=False,
                    is_block_quote=False,
                    embedding=vector,
                    model_id=settings.embedding_model_id,
                )
            )
        await session.commit()
        match = await best_supporting_passage(
            session, [own], target, settings, own_chapter_index=1, matched_chapter_index=2
        )
        assert match is not None and match.level is None
        assert match.similarity == pytest.approx(0.2, abs=1e-6)
        assert match.matched_manuscript_id == target and match.matched_chapter_index == 2
        detail = _supporting_passage_detail(match)
        record = CheckResult(
            check_run_id=target_run,
            kind=CheckKind.originality_reuse,
            outcome=ResultOutcome.passed,
            detail=detail,
        )
        session.add(record)
        await session.commit()
        async with session_factory() as reader:
            recorded = await reader.scalar(
                select(CheckResult.detail).where(CheckResult.id == record.id)
            )
        pair = _passage_pair_from_detail(
            "Parent coarse comparison",
            {"kind": "reuse_exact_duplicate", "supporting_passage": recorded},
        )
        assert pair is not None and pair.model_dump(mode="json")["level"] is None
        assert (
            pair.own_context_before == "Before own."
            and pair.matched_context_after == "After stored."
        )
        assert (
            await best_supporting_passage(
                session, [own], target, settings, own_chapter_index=99, matched_chapter_index=2
            )
            is None
        )
        # The stronger wrong-manuscript/chapter examples do not contaminate the
        # scoped support; standalone retrieval keeps its independent threshold.
        assert match.matched_page == 4 and match.own_page == 3
        await session.execute(
            delete(ManuscriptPassageArchive).where(
                ManuscriptPassageArchive.manuscript_id == target,
                ManuscriptPassageArchive.chapter_index == 3,
            )
        )
        assert (
            await _best_passage_match_for(
                session,
                own,
                exclude_manuscript_ids={rival},
                include_reference_list=False,
                include_block_quote=False,
                settings=settings,
            )
            is None
        )


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


async def _add_to_library(
    session_factory,
    manuscript_id: int,
    extraction: ExtractionResult,
    *,
    legacy_document_only: bool = False,
) -> None:
    """V-077 (D-028): a check no longer writes into the corpus; a manuscript
    becomes a comparison source only by entering the Library. Mirrors
    `app.library.service._activate`: store its embeddings, mark its entry
    active. Replaces what the pre-D-028 write-back after a check used to do
    implicitly, so each test still seeds exactly the corpus it reasons about."""
    settings = get_settings()
    _write_raw_store(extraction, raw_store_path(settings, manuscript_id))
    async with session_factory() as session:
        await store_document_embeddings(
            session, manuscript_id, compute_document_embeddings(extraction, settings)
        )
        passages = [] if legacy_document_only else compute_passage_embeddings(extraction, settings)
        if passages:
            await store_passage_embeddings(session, manuscript_id, passages, settings)
        session.add(
            LibraryEntry(
                manuscript_id=manuscript_id,
                source=LibraryEntrySource.library_submission,
                status=LibraryEntryStatus.active,
                added_at=datetime.now(UTC),
            )
        )
        await session.commit()


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
        result = await _run_with_saved_source(
            session, manuscript_id, check_run_id, _extraction(CH1, CH2), settings
        )
        # V-077 (D-028): the check compares against the Library and never
        # writes the checked manuscript into it.
        corpus_rows = await session.scalar(
            text(
                "SELECT (SELECT count(*) FROM manuscript_archive WHERE manuscript_id = :id)"
                " + (SELECT count(*) FROM manuscript_passage_archive WHERE manuscript_id = :id)"
            ),
            {"id": manuscript_id},
        )
    assert result.outcome == ResultOutcome.not_applicable
    assert result.detail["coverage_reason"] == "no_comparison_archive"
    assert result.detail["archive_size_n"] == 0
    assert result.detail["corpus"] == "library"
    assert result.detail["n_flags"] == 0
    assert result.detail["passages_found"] > 0
    assert "passages_archived" not in result.detail
    assert corpus_rows == 0


async def test_bug180_passage_truncation_is_recorded_honestly_not_silently(
    session_factory, monkeypatch
):
    """backend review finding (BUG-180): a truncated corpus write with no
    trace anywhere reproduces BUG-096's already-fixed failure class (a
    partial result rendered indistinguishable from a complete one). Since
    V-077 the only corpus write is a manuscript entering the Library, so the
    honesty record lives on `library_entry_added`.
    `reuse_max_passages_per_manuscript=0` forces every real passage to be
    truncated: a decisive proof that found/stored genuinely diverge and are
    both recorded."""
    from app.library.service import _activate

    monkeypatch.setenv("REUSE_MAX_PASSAGES_PER_MANUSCRIPT", "0")
    get_settings.cache_clear()
    settings = get_settings()
    manuscript_id, _ = await _seed_manuscript_and_run(session_factory, group_label="Group A")
    extraction = _extraction(CH1, CH2)
    async with session_factory() as session:
        manuscript = await session.get(Manuscript, manuscript_id)
        entry = LibraryEntry(
            manuscript_id=manuscript_id,
            source=LibraryEntrySource.library_submission,
            status=LibraryEntryStatus.processing,
        )
        session.add(entry)
        await session.commit()
        await _activate(
            session,
            entry,
            manuscript,
            compute_document_embeddings(extraction, settings),
            compute_passage_embeddings(extraction, settings),
            settings,
            expected=LibraryEntryStatus.processing,
            decision=None,
        )
        payload = await session.scalar(
            text("SELECT payload FROM audit_log WHERE event_type = 'library_entry_added'")
        )
        stored = await session.scalar(
            text("SELECT count(*) FROM manuscript_passage_archive WHERE manuscript_id = :id"),
            {"id": manuscript_id},
        )
    assert payload["passages_found"] > 0
    assert payload["passages_stored"] == 0
    assert stored == 0
    get_settings.cache_clear()


async def test_reuploaded_duplicate_produces_a_high_severity_flag(session_factory):
    """Ticket AC (V5 demo seed): re-uploading the same manuscript
    (renamed) -> exact-dup flag."""
    settings = get_settings()

    # First upload.
    manuscript_a, check_run_a = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    async with session_factory() as session:
        await _run_with_saved_source(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )
        await _add_to_library(session_factory, manuscript_a, _extraction(CH1, CH2))

    # Same content, "renamed" (different group_label, different manuscript row).
    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group A (resubmission)"
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
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
        await _run_with_saved_source(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )
        await _add_to_library(session_factory, manuscript_a, _extraction(CH1, CH2))

    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group A (resubmission)"
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
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
    """No detected chapters; verified body text still supports passage scope -1."""
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
    evidenced, it should not be high severity." An active legacy Library
    record can contain only document embeddings, without passage evidence.
    Its lead must retain the fallback downgrade. Reuse leads are non-scoring
    regardless of their persisted severity."""
    settings = get_settings()

    manuscript_a, check_run_a = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    async with session_factory() as session:
        await _run_with_saved_source(
            session, manuscript_a, check_run_a, _extraction_flat_no_chapters(CH1), settings
        )
        await _add_to_library(
            session_factory,
            manuscript_a,
            _extraction_flat_no_chapters(CH1),
            legacy_document_only=True,
        )
        assert (
            await session.scalar(
                text("SELECT count(*) FROM manuscript_passage_archive WHERE manuscript_id = :id"),
                {"id": manuscript_a},
            )
            == 0
        )
        assert (
            await session.scalar(
                text("SELECT count(*) FROM manuscript_archive WHERE manuscript_id = :id"),
                {"id": manuscript_a},
            )
            == 1
        )

    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group A (resubmission)"
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
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


async def test_flat_document_library_match_preserves_passage_evidence_without_scoring(
    session_factory,
):
    settings = get_settings()
    source_id, _ = await _seed_manuscript_and_run(session_factory, group_label="Library source")
    await _add_to_library(session_factory, source_id, _extraction_flat_no_chapters(CH1))
    manuscript_id, run_id = await _seed_manuscript_and_run(session_factory, group_label="Check")
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session, manuscript_id, run_id, _extraction_flat_no_chapters(CH1), settings
        )
        rows = (
            await session.execute(
                text(
                    "SELECT evidence_excerpt, page_anchor, detail FROM flag "
                    "WHERE check_result_id = :id"
                ),
                {"id": result.id},
            )
        ).all()
    passage_rows = [row for row in rows if row.detail["kind"].endswith("_passage")]
    assert len(passage_rows) == 1
    passage = passage_rows[0]
    assert passage.evidence_excerpt == CH1
    assert passage.page_anchor == "p. 1"
    assert passage.detail["matched_text"] == CH1
    assert passage.detail["matched_manuscript_id"] == source_id
    assert all(exclusion_reason("originality_reuse", row.detail) is not None for row in rows)
    whole = [row for row in rows if not row.detail["kind"].endswith("_passage")]
    assert len(whole) == 1
    assert whole[0].detail["supporting_passage"]["own_text"] == CH1
    assert whole[0].detail["supporting_passage"]["matched_text"] == CH1


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
        await _run_with_saved_source(
            session,
            manuscript_a,
            check_run_a,
            _extraction(CH1, CH2),
            settings,
            instructor_id=instructor_id,
            content_hash=None,
        )
        await _add_to_library(session_factory, manuscript_a, _extraction(CH1, CH2))

    manuscript_b, check_run_b = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=None
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
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
    # V-077: names a Library entry, never "an earlier upload" (only Library
    # entries are comparison sources now).
    assert "your own Library records" in rows[0].reason
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
        await _run_with_saved_source(
            session,
            manuscript_a,
            check_run_a,
            _extraction(CH1, CH2),
            settings,
            instructor_id=instructor_id,
            content_hash=None,
        )
        await _add_to_library(session_factory, manuscript_a, _extraction(CH1, CH2))

    # A genuinely different manuscript, same instructor, unrelated content --
    # must never be pulled into the archive as a "match" by this test's own
    # construction (no third manuscript uploaded), just establishing the
    # instructor has more than one prior manuscript on record.
    other_manuscript, other_check_run = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=None
    )
    async with session_factory() as session:
        await _run_with_saved_source(
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
        await _add_to_library(
            session_factory,
            other_manuscript,
            _extraction(
                "An entirely unrelated introduction about renewable energy policy.",
                "An entirely unrelated methodology about survey sampling.",
            ),
        )

    # Re-upload of the FIRST manuscript's content -- must resubmission-collapse
    # against manuscript_a specifically, not against the unrelated one.
    manuscript_c, check_run_c = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=None
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
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
    column existed).

    V-077 (D-028): only ACTIVE Library entries count. `earlier_dashboard_copy`
    is the same instructor's same bytes with the LOWEST id, but it was never
    added to the Library, so it is not a comparison source and must never
    be named as one."""
    instructor_id = await _seed_instructor(session_factory)
    other_instructor_id = await _seed_instructor(session_factory)
    same_hash = "a" * 64

    async with session_factory() as session:
        earlier_dashboard_copy = Manuscript(
            instructor_id=instructor_id,
            group_label="Group A",
            file_ref="0.pdf",
            content_hash=same_hash,
        )
        session.add(earlier_dashboard_copy)
        await session.commit()
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
        for in_library in (mine, mine_no_match, purged_twin, others_twin):
            session.add(
                LibraryEntry(
                    manuscript_id=in_library.id,
                    source=LibraryEntrySource.library_submission,
                    status=LibraryEntryStatus.active,
                )
            )
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
        # Lowest-id ACTIVE Library match, deterministic; the lower-id
        # dashboard copy outside the Library is skipped.
        assert found == mine.id
        assert earlier_dashboard_copy.id < mine.id

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
    """backend review finding (BUG-140 review): suppressing the FLAGS
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
        await _run_with_saved_source(
            session,
            manuscript_a,
            check_run_a,
            _extraction(CH1, CH2),
            settings,
            instructor_id=instructor_id,
            content_hash=None,
        )
        await _add_to_library(session_factory, manuscript_a, _extraction(CH1, CH2))

    for _ in range(2):  # two more resubmissions of the identical content
        manuscript_n, check_run_n = await _seed_manuscript_in_group(
            session_factory, instructor_id=instructor_id, group_id=None
        )
        async with session_factory() as session:
            result = await _run_with_saved_source(
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
        await _run_with_saved_source(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )
        await _add_to_library(session_factory, manuscript_a, _extraction(CH1, CH2))

    # manuscript_b's instructor has NO other manuscript yet -- textbook
    # first upload.
    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group B"
    )
    async with session_factory() as session:
        result_b = await _run_with_saved_source(
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
    # UX review finding (2026-08-24): the banner is the sole disclosure
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
        result_c = await _run_with_saved_source(
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
        await _run_with_saved_source(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )
        await _add_to_library(session_factory, manuscript_a, _extraction(CH1, CH2))

    # Same team, same group, a revised/re-uploaded manuscript row.
    manuscript_b, check_run_b = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=group_id
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
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
        await _run_with_saved_source(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )
        await _add_to_library(session_factory, manuscript_a, _extraction(CH1, CH2))

    manuscript_b, check_run_b = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=group_b
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
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
        await _run_with_saved_source(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )
        await _add_to_library(session_factory, manuscript_a, _extraction(CH1, CH2))

    # A genuinely different team, also defaulted to "Ungrouped".
    manuscript_b, check_run_b = await _seed_manuscript_in_group(
        session_factory, instructor_id=instructor_id, group_id=ungrouped_id
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
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
    chapter heading text (first-time-user review reproduced both leaking into a
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
        await _run_with_saved_source(
            session,
            manuscript_a,
            check_run_a,
            _extraction_titled(other_ch1_title, CH1, other_ch2_title, CH2),
            settings,
        )
        await _add_to_library(
            session_factory,
            manuscript_a,
            _extraction_titled(other_ch1_title, CH1, other_ch2_title, CH2),
        )

    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group B"
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
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
        await _run_with_saved_source(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )
        await _add_to_library(session_factory, manuscript_a, _extraction(CH1, CH2))

    # Manuscript B: chapter 1 is CH1 verbatim (transplanted), chapter 2 is
    # genuinely unrelated content — the WHOLE-doc vector should be diluted
    # enough to miss the whole-doc threshold, but the chapter-level query
    # must still catch the transplanted chapter specifically.
    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group B"
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
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
    """backend review finding (BUG-153 review, live-reproduced): without
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
        await _run_with_saved_source(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2_EXTENDED), settings
        )
        await _add_to_library(session_factory, manuscript_a, _extraction(CH1, CH2_EXTENDED))

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
        result = await _run_with_saved_source(
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
        await _run_with_saved_source(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )
        await _add_to_library(session_factory, manuscript_a, _extraction(CH1, CH2))

    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group C"
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session,
            manuscript_b,
            check_run_b,
            _extraction(same_topic_different_wording, different_ch2),
            settings,
        )
    assert result.detail["n_flags"] == 0


async def test_a_check_never_writes_into_the_corpus_or_matches_self(session_factory):
    """V-077 (D-028) replaces F7.3's write-back: a check compares against the
    Library and adds nothing to it, so the checked manuscript never appears as
    a match against itself and the corpus gains no row from a check."""
    settings = get_settings()
    manuscript_id, check_run_id = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session, manuscript_id, check_run_id, _extraction(CH1, CH2), settings
        )
    assert result.detail["n_flags"] == 0  # never matched itself

    async with session_factory() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM manuscript_archive WHERE manuscript_id = :id"),
            {"id": manuscript_id},
        )
    assert count == 0


async def test_bug_151_writes_a_check_computed_audit_event_with_real_matched_ids(
    session_factory,
):
    """BUG-151: F7 made zero LLM calls, so it wrote nothing to the audit
    log at all -- the check that forced 82 of one production run's 99
    flags had no record of how its verdict was reached (charter judgment
    4). A real `originality_reuse_check_computed` row must exist,
    carrying the thresholds in force and the OPAQUE matched manuscript
    ids (never `matched_group_label`, the BUG-050 identity-leak guard
    extended to the audit log, not assumed to not apply there)."""
    settings = get_settings()
    manuscript_a, check_run_a = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    async with session_factory() as session:
        await _run_with_saved_source(
            session, manuscript_a, check_run_a, _extraction(CH1, CH2), settings
        )
        await _add_to_library(session_factory, manuscript_a, _extraction(CH1, CH2))

    manuscript_b, check_run_b = await _seed_manuscript_and_run(
        session_factory, group_label="Group B"
    )
    async with session_factory() as session:
        await _run_with_saved_source(
            session, manuscript_b, check_run_b, _extraction(CH1, CH2), settings
        )
        await _add_to_library(session_factory, manuscript_b, _extraction(CH1, CH2))

    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT payload FROM audit_log WHERE check_run_id = :id "
                    "AND event_type = 'originality_reuse_check_computed'"
                ),
                {"id": check_run_b},
            )
        ).first()
    assert row is not None
    payload = row.payload
    assert payload["matched_manuscript_ids"] == [manuscript_a]
    assert payload["thresholds"]["exact_duplicate"] == settings.reuse_exact_duplicate_threshold
    assert payload["n_flags"] > 0
    assert "matched_group_label" not in payload  # BUG-050: never leaked, even here


async def test_bug_151_no_embeddable_content_still_writes_a_check_computed_audit_event(
    session_factory,
):
    """BUG-151 (backend review: the diff's own test only covered the main
    path): the early "nothing to embed" branch (an image-only re-scan, or
    any manuscript with no chapter text at all) must also leave a real
    audit row, not just the resubmission-flag path already covered above."""
    manuscript_id, check_run_id = await _seed_manuscript_and_run(
        session_factory, group_label="Group A"
    )
    empty_extraction = ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=True,
        text_chars=0,
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=[],
        images=[],
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session, manuscript_id, check_run_id, empty_extraction, get_settings()
        )
        assert result.detail["note"] == "No embeddable content was extracted from this manuscript."

        row = (
            await session.execute(
                text(
                    "SELECT payload FROM audit_log WHERE check_run_id = :id "
                    "AND event_type = 'originality_reuse_check_computed'"
                ),
                {"id": check_run_id},
            )
        ).first()
    assert row is not None
    assert row.payload["outcome"] == "not_applicable"
    assert row.payload["matched_manuscript_ids"] == []


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
        first = await _run_with_saved_source(
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
    assert count == 0  # V-077: a check never writes into the corpus


async def test_pages_after_an_unclear_reference_list_are_named_not_passed(session_factory):
    """Comparison leaves out everything from a reference list whose end could
    not be found; the result says which pages instead of reading as passed."""
    from tests.test_ingest_reference_boundaries import unclear_end_manuscript

    manuscript_id, run_id = await _seed_manuscript_and_run(
        session_factory, group_label="Unclear end"
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session, manuscript_id, run_id, unclear_end_manuscript(), get_settings()
        )
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["coverage_reason"] == "reference_end_unclear"
    assert result.detail["pages_not_compared"] == [2, 3, 4]
    assert result.detail["not_compared_from"] == "p. 2"


async def test_a_word_file_with_an_unclear_reference_list_is_not_passed(session_factory):
    """A Word file has no pages to name, but the text after an unclear
    reference list is still left out, so the result says where it starts."""
    from tests.test_ingest_reference_boundaries import as_word_document, unclear_end_manuscript

    manuscript_id, run_id = await _seed_manuscript_and_run(
        session_factory, group_label="Unclear end, Word"
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session,
            manuscript_id,
            run_id,
            as_word_document(unclear_end_manuscript()),
            get_settings(),
        )
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["coverage_reason"] == "reference_end_unclear"
    assert result.detail["pages_not_compared"] == []
    assert result.detail["not_compared_from"].startswith("¶")


def _word_short_paper_with_an_equation() -> ExtractionResult:
    """A Word short paper whose list end is unclear and which holds one native
    equation, so the equation limit also applies."""
    import hashlib
    from io import BytesIO

    from docx import Document
    from docx.oxml import parse_xml

    from app.ingest.docx_math import inventory_native_math
    from tests.test_ingest_reference_boundaries import as_word_document, unclear_end_manuscript

    word = as_word_document(
        unclear_end_manuscript().model_copy(
            update={"section_tree": SectionTree(source="none", nodes=[])}
        )
    )
    document = Document()
    for block in word.blocks:
        document.add_paragraph(block.text)
    document.add_paragraph("")._p.append(
        parse_xml(
            '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
            "<m:r><m:t>s = a + b</m:t></m:r></m:oMath>"
        )
    )
    stream = BytesIO()
    document.save(stream)
    digest = hashlib.sha256(stream.getvalue()).hexdigest()
    return word.model_copy(
        update={
            "docx_source_sha256": digest,
            "native_math_inventory": inventory_native_math(document, digest),
        }
    )


def _pdf_short_paper_with_an_unresolved_tail() -> ExtractionResult:
    """Repeated list numbers, and a later appendix whose membership in the
    list cannot be settled."""
    rows = [
        (1, "The clinic booking system was evaluated with twenty staff over six weeks. " * 12),
        (2, "REFERENCES"),
        (2, "[1] Reyes, J. (2024). Booking queues in rural clinics. Journal of Examples, 4(2)."),
        (2, "[1] Santos, A. (2023). Measuring appointment no-shows. Journal of Examples, 5(1)."),
        (3, "APPENDIX A"),
        (3, "Cruz, M. (2021). Mobile queueing for barangay health centers. Notes, 7(1), 1-9."),
    ]
    blocks = [
        TextBlock(
            page=page,
            bbox=(72, 100 + index * 30, 540, 120 + index * 30),
            text=text,
            max_font_size=16 if text.isupper() else 11,
            bold_ratio=1 if text.isupper() else 0,
        )
        for index, (page, text) in enumerate(rows)
    ]
    return ExtractionResult(
        page_count=3,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(block.text) for block in blocks),
        blocks=blocks,
        images=[],
        section_tree=SectionTree(source="none", nodes=[]),
    )


@pytest.mark.parametrize(
    "build", [_word_short_paper_with_an_equation, _pdf_short_paper_with_an_unresolved_tail]
)
async def test_a_refused_body_keeps_its_reason_when_another_limit_also_applies(
    session_factory, build
):
    """An equation or an unresolved tail takes over the coverage reason, but
    the result must still say the body was refused, not that it had no text."""
    manuscript_id, run_id = await _seed_manuscript_and_run(
        session_factory, group_label=f"Refused body {build.__name__}"
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session, manuscript_id, run_id, build(), get_settings()
        )
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["coverage_reason"] != "reference_boundary_unclear"
    assert result.detail["not_compared_reason"] == "reference_boundary_unclear"
    assert "no embeddable content" not in result.detail["note"].casefold()


async def test_a_body_that_cannot_be_told_from_its_references_is_named_not_empty(
    session_factory,
):
    """A short paper whose bibliography is ambiguous is not compared at all.
    That is said as such, with every page that holds text, never as a file
    with no content."""
    from tests.test_reuse_unstructured_scope import ancillary_source, block

    extraction = ancillary_source()
    extraction.blocks[-1:] = [
        block("[1] Reyes, J. (2024). One work.", 3),
        block("[1] Santos, A. (2023). A different work.", 3),
    ]
    manuscript_id, run_id = await _seed_manuscript_and_run(
        session_factory, group_label="Short paper, ambiguous list"
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session, manuscript_id, run_id, extraction, get_settings()
        )
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["coverage_reason"] == "reference_boundary_unclear"
    assert result.detail["pages_not_compared"] == [1, 2, 3]
    assert result.detail["comparison_ran"] is False
    assert "no embeddable content" not in result.detail["note"].casefold()


@pytest.mark.parametrize("level", ["high_similarity", "exact_duplicate"])
async def test_a_similar_section_with_no_shared_wording_is_counted_not_raised(
    session_factory, level
):
    """BUG-620: with the similarity bars lowered so the vectors match, text on
    the same topic in different words raises nothing at any granularity or
    level, and is counted; a real copy of the same Library text is raised."""
    bars = {
        "reuse_high_similarity_threshold": 0.3,
        "reuse_passage_high_similarity_threshold": 0.3,
    }
    if level == "exact_duplicate":
        bars |= {
            "reuse_exact_duplicate_threshold": 0.31,
            "reuse_passage_exact_duplicate_threshold": 0.31,
        }
    settings = get_settings().model_copy(update=bars)
    library_text = (
        "Attendance in most classrooms is still recorded on paper sign-in sheets that the "
        "instructor tallies by hand after each session, which wastes class time and leads "
        "to frequent recording mistakes in the final report."
    )
    same_topic = (
        "Many teachers keep track of who came to class with a printed list that students "
        "initial, then count the marks later; this slows lessons down and errors creep "
        "into the records that are submitted at the end of the term."
    )
    other_chapter = (
        "Our grading pipeline layers automated rule checks underneath a reviewer model, "
        "escalating uncertain cases to the instructor rather than deciding on its own."
    )
    source_id, source_run = await _seed_manuscript_and_run(
        session_factory, group_label=f"Source {level}"
    )
    await _add_to_library(session_factory, source_id, _extraction(library_text, CH2))

    topic_id, topic_run = await _seed_manuscript_and_run(
        session_factory, group_label=f"Topic {level}"
    )
    async with session_factory() as session:
        topic = await _run_with_saved_source(
            session, topic_id, topic_run, _extraction(same_topic, other_chapter), settings
        )
    copy_id, copy_run = await _seed_manuscript_and_run(session_factory, group_label=f"Copy {level}")
    async with session_factory() as session:
        copied = await _run_with_saved_source(
            session, copy_id, copy_run, _extraction(library_text, other_chapter), settings
        )
    assert topic.detail["n_similar_without_shared_wording"] >= 1
    # Nothing at all: no passage finding, and no section finding resting on
    # the topic-only passage the fallback search would otherwise pick.
    assert await _flag_kinds(session_factory, topic.id) == []
    assert any(kind.endswith("_passage") for kind in await _flag_kinds(session_factory, copied.id))
    await _assert_counts_match_flags(session_factory, copied)
    # A withheld section is in neither count.
    await _assert_counts_match_flags(session_factory, topic)


async def _assert_counts_match_flags(session_factory, result) -> None:
    """`n_flags` and the audit's matched records are the Flag rows' own."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT (detail->>'matched_manuscript_id')::bigint AS matched FROM flag "
                    "WHERE check_result_id = :id"
                ),
                {"id": result.id},
            )
        ).all()
        payload = await session.scalar(
            text(
                "SELECT payload FROM audit_log WHERE check_run_id = :run "
                "AND event_type = 'originality_reuse_check_computed'"
            ),
            {"run": result.check_run_id},
        )
    assert result.detail["n_flags"] == len(rows)
    assert sorted(payload["matched_manuscript_ids"]) == sorted({row.matched for row in rows})


async def _flag_kinds(session_factory, result_id: int) -> list[str]:
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    text("SELECT detail->>'kind' FROM flag WHERE check_result_id = :id"),
                    {"id": result_id},
                )
            ).all()
        )


async def test_a_section_match_whose_only_evidence_is_below_the_passage_bar_is_not_raised(
    session_factory,
):
    """BUG-620: a section sitting near the similarity bar on topic, with a
    weaker passage (shared standard text) as its only evidence, is counted,
    not reported as reuse. The same shared sentence is planted on both sides."""
    shared = (
        "Dependability is the extent to which a campus system keeps working for the "
        "people who rely on it during enrolment, grading and every other busy week."
    )
    settings = get_settings().model_copy(
        update={
            "reuse_high_similarity_threshold": 0.3,
            "reuse_passage_high_similarity_threshold": 0.99,
            "reuse_passage_exact_duplicate_threshold": 0.995,
        }
    )
    source_id, _ = await _seed_manuscript_and_run(session_factory, group_label="Standard A")
    await _add_to_library(
        session_factory,
        source_id,
        _extraction(CH1 + " " + shared, "A different second chapter about the server setup."),
    )
    own_id, own_run = await _seed_manuscript_and_run(session_factory, group_label="Standard B")
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session,
            own_id,
            own_run,
            _extraction(
                "Students in the pilot section answered a survey about the timetable. " + shared,
                "Another unrelated chapter on deployment to the campus network.",
            ),
            settings,
        )
    kinds = await _flag_kinds(session_factory, result.id)
    assert not any(kind.startswith("reuse_high_similarity") for kind in kinds)
    assert result.detail["n_similar_without_shared_wording"] >= 1


async def test_a_section_with_no_passage_evidence_keeps_its_existing_path(session_factory):
    """BUG-620 is about shared wording; a section with no passage to check
    (a Library record stored without passages) is raised as before, at
    Medium, not silenced."""
    settings = get_settings().model_copy(update={"reuse_high_similarity_threshold": 0.3})
    source_id, _ = await _seed_manuscript_and_run(session_factory, group_label="No passages")
    await _add_to_library(
        session_factory, source_id, _extraction(CH1, CH2), legacy_document_only=True
    )
    own_id, own_run = await _seed_manuscript_and_run(session_factory, group_label="Checked")
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session, own_id, own_run, _extraction(CH1, CH2), settings
        )
    kinds = await _flag_kinds(session_factory, result.id)
    assert any(not kind.endswith("_passage") for kind in kinds)


async def test_a_nearer_topic_match_does_not_hide_the_real_source(session_factory):
    """BUG-620: the nearest Library passage holds every word in another order
    (the same vector, no shared runs); the real source ranks below it. The
    real source is found when the next candidates are tried, and lost when
    only the nearest is."""
    copied = (
        "The clinic queue prototype sent each patient a text message when their turn was "
        "near, and the nurses reported that the waiting room was calmer during the trial "
        "weeks than in the month before it started at the barangay health centre."
    )
    tokens = copied.split()
    shuffled = " ".join(tokens[1::2] + tokens[0::2])
    source_text = copied + " Patients also asked for reminders in Filipino as well as English."
    settings = get_settings().model_copy(update={"reuse_passage_high_similarity_threshold": 0.5})

    decoy_id, _ = await _seed_manuscript_and_run(session_factory, group_label="Shuffled")
    await _add_to_library(session_factory, decoy_id, _extraction(shuffled, CH2))
    source_id, _ = await _seed_manuscript_and_run(session_factory, group_label="Real source")
    await _add_to_library(session_factory, source_id, _extraction(source_text, CH2.upper()))

    found = {}
    for candidates in (1, 5):
        own_id, own_run = await _seed_manuscript_and_run(
            session_factory, group_label=f"Copier {candidates}"
        )
        async with session_factory() as session:
            result = await _run_with_saved_source(
                session,
                own_id,
                own_run,
                _extraction(copied, "An unrelated chapter on deployment to the campus network."),
                settings.model_copy(update={"reuse_confirm_candidates": candidates}),
            )
            found[candidates] = set(
                (
                    await session.scalars(
                        text(
                            "SELECT (detail->>'matched_manuscript_id')::bigint FROM flag "
                            "WHERE check_result_id = :id AND detail->>'kind' LIKE '%_passage'"
                        ),
                        {"id": result.id},
                    )
                ).all()
            )
    assert source_id in found[5] and decoy_id not in found[5]
    assert source_id not in found[1]


# --- BUG-662: another upload of the same work ---------------------------------


async def _seed_upload(
    session_factory,
    *,
    instructor_id: int,
    group_id: int | None,
    group_label: str = DEFAULT_GROUP_LABEL,
    original_filename: str | None = None,
    content_hash: str | None = None,
) -> tuple[int, int]:
    async with session_factory() as session:
        manuscript = Manuscript(
            instructor_id=instructor_id,
            group_id=group_id,
            group_label=group_label,
            file_ref="test.pdf",
            original_filename=original_filename,
            content_hash=content_hash,
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


async def _flags(session_factory, result_id: int):
    async with session_factory() as session:
        return (
            await session.execute(
                text("SELECT severity, detail FROM flag WHERE check_result_id = :id ORDER BY id"),
                {"id": result_id},
            )
        ).all()


def _no_near_exact_rule():
    # Text identical enough to trip the near-exact rule would hide what the
    # name rule does on its own; this keeps every match below that bar.
    return get_settings().model_copy(update={"reuse_exact_duplicate_threshold": 1.01})


async def test_ungrouped_reupload_without_a_hash_is_one_low_same_work_lead(session_factory):
    """Production runs 11/12/17/18: the team's own thesis, uploaded again as
    "VERIDICAL-DOCUMENTATION (1).pdf" under "Ungrouped", matched the earlier
    upload (no stored hash) at every passage. Ungrouped siblings are not
    excluded by group, so the name rule has to recognise it."""
    settings = _no_near_exact_rule()
    instructor_id = await _seed_instructor(session_factory)
    ungrouped = await _make_group(
        session_factory, instructor_id=instructor_id, name=DEFAULT_GROUP_LABEL
    )
    earlier, _ = await _seed_upload(
        session_factory,
        instructor_id=instructor_id,
        group_id=ungrouped,
        original_filename="VERIDICAL-DOCUMENTATION.pdf",
    )
    await _add_to_library(session_factory, earlier, _extraction(CH1, CH2))
    again, again_run = await _seed_upload(
        session_factory,
        instructor_id=instructor_id,
        group_id=ungrouped,
        original_filename="VERIDICAL-DOCUMENTATION (1).pdf",
        content_hash="b" * 64,
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session,
            again,
            again_run,
            _extraction(CH1, CH2),
            settings,
            instructor_id=instructor_id,
            content_hash="b" * 64,
        )
    rows = await _flags(session_factory, result.id)
    assert [(r.severity, r.detail["kind"]) for r in rows] == [
        ("low", "reuse_same_instructor_resubmission")
    ]
    detail = rows[0].detail
    assert detail["matched_manuscript_ids"] == [earlier]
    assert detail["same_work"][0]["basis"] == "same_file_name"
    assert detail["affects_readiness"] is False
    assert exclusion_reason("originality_reuse", detail) is not None
    assert f"#{earlier}" in detail["reason"]
    assert result.detail["n_flags"] == 1
    assert result.detail["n_same_work_sources"] == 1
    # Counted as compared (by name and whole text), so the report never says
    # "nothing to compare against" beside a lead naming it.
    assert result.detail["archive_size_n"] == 1
    assert result.outcome == ResultOutcome.passed


async def test_every_copy_of_the_same_work_is_collapsed_not_just_one(session_factory):
    """Production runs 17/18: the hash rule collapsed one record, while two
    legacy copies of the same thesis (no hash) still raised 41 high leads.
    Every own copy is named in the one lead and none raises a lead itself."""
    settings = get_settings()
    instructor_id = await _seed_instructor(session_factory)
    team_a = await _make_group(
        session_factory, instructor_id=instructor_id, name="VERIDICAL Capstone Team"
    )
    team_b = await _make_group(session_factory, instructor_id=instructor_id, name="VERIDICAL")
    same_file, _ = await _seed_upload(
        session_factory,
        instructor_id=instructor_id,
        group_id=None,
        original_filename="VERIDICAL-DOCUMENTATION.pdf",
        content_hash="c" * 64,
    )
    legacy_a, _ = await _seed_upload(
        session_factory,
        instructor_id=instructor_id,
        group_id=team_a,
        group_label="VERIDICAL Capstone Team",
        original_filename="VERIDICAL-DOCUMENTATION.pdf",
    )
    legacy_b, _ = await _seed_upload(
        session_factory,
        instructor_id=instructor_id,
        group_id=team_b,
        group_label="VERIDICAL",
        original_filename="VERIDICAL-DOCUMENTATION.pdf",
    )
    for record in (same_file, legacy_a, legacy_b):
        await _add_to_library(session_factory, record, _extraction(CH1, CH2))
    checked, checked_run = await _seed_upload(
        session_factory,
        instructor_id=instructor_id,
        group_id=None,
        original_filename="VERIDICAL-DOCUMENTATION (1).pdf",
        content_hash="c" * 64,
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session,
            checked,
            checked_run,
            _extraction(CH1, CH2),
            settings,
            instructor_id=instructor_id,
            content_hash="c" * 64,
        )
        audit = await session.scalar(
            text(
                "SELECT payload FROM audit_log WHERE event_type = "
                "'originality_reuse_check_computed' AND check_run_id = :id"
            ),
            {"id": checked_run},
        )
    rows = await _flags(session_factory, result.id)
    assert [(r.severity, r.detail["kind"]) for r in rows] == [
        ("low", "reuse_same_instructor_resubmission")
    ]
    detail = rows[0].detail
    assert detail["matched_manuscript_ids"] == [same_file, legacy_a, legacy_b]
    assert [s["basis"] for s in detail["same_work"]] == [
        "same_file",
        "near_identical_text",
        "near_identical_text",
    ]
    for record in (same_file, legacy_a, legacy_b):
        assert f"#{record}" in detail["reason"]
    assert audit["same_work_source_ids"] == [same_file, legacy_a, legacy_b]
    assert audit["matched_manuscript_ids"] == sorted([same_file, legacy_a, legacy_b])


async def test_an_own_copy_does_not_hide_a_real_source_behind_it(session_factory):
    """Whole-document and passage queries keep only the nearest record. Left
    in, the team's own copy at similarity 1.0 would win every one of them and
    a real outside source would never be reported."""
    settings = get_settings()
    instructor_id = await _seed_instructor(session_factory)
    own_copy, _ = await _seed_upload(
        session_factory,
        instructor_id=instructor_id,
        group_id=None,
        original_filename="clinic-queue.pdf",
    )
    await _add_to_library(session_factory, own_copy, _extraction(CH1, CH2))
    outside, _ = await _seed_manuscript_and_run(session_factory, group_label="Another school")
    await _add_to_library(session_factory, outside, _extraction(CH1, CH2))
    checked, checked_run = await _seed_upload(
        session_factory,
        instructor_id=instructor_id,
        group_id=None,
        original_filename="clinic-queue.pdf",
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session,
            checked,
            checked_run,
            _extraction(CH1, CH2),
            settings,
            instructor_id=instructor_id,
        )
    rows = await _flags(session_factory, result.id)
    same_work = [r for r in rows if r.detail["kind"] == "reuse_same_instructor_resubmission"]
    leads = [r for r in rows if r.detail["kind"] != "reuse_same_instructor_resubmission"]
    assert [r.detail["matched_manuscript_ids"] for r in same_work] == [[own_copy]]
    assert leads
    assert {r.detail["matched_manuscript_id"] for r in leads} == {outside}
    assert result.detail["archive_size_n"] == 2


async def test_different_work_from_the_same_instructor_still_raises_leads(session_factory):
    """Control: similar text from the same instructor's Library stays a lead
    when nothing says it is the same work: different file names and teams, a
    shared but generic file name, both sides "Ungrouped" (a shared bucket, not
    a team), or another instructor's identically named upload."""
    settings = _no_near_exact_rule()
    instructor_id = await _seed_instructor(session_factory)
    alpha = await _make_group(session_factory, instructor_id=instructor_id, name="Team Alpha")
    beta = await _make_group(session_factory, instructor_id=instructor_id, name="Team Beta")
    ungrouped = await _make_group(
        session_factory, instructor_id=instructor_id, name=DEFAULT_GROUP_LABEL
    )
    loose = DEFAULT_GROUP_LABEL
    cases = [
        ("alpha-attendance.pdf", "beta-attendance.pdf", alpha, beta, "Team Alpha", "Team Beta"),
        ("Thesis Final.pdf", "thesis final v2.pdf", alpha, beta, "Team Alpha", "Team Beta"),
        ("alpha-clinic.pdf", "beta-clinic.pdf", ungrouped, ungrouped, loose, loose),
    ]
    for library_name, checked_name, library_group, checked_group, lib_label, label in cases:
        async with session_factory() as session:
            await session.execute(
                text(
                    "TRUNCATE library_entry, manuscript_archive, manuscript_chapter_archive, "
                    "manuscript_passage_archive"
                )
            )
            await session.commit()
        record, _ = await _seed_upload(
            session_factory,
            instructor_id=instructor_id,
            group_id=library_group,
            group_label=lib_label,
            original_filename=library_name,
        )
        await _add_to_library(session_factory, record, _extraction(CH1, CH2))
        checked, checked_run = await _seed_upload(
            session_factory,
            instructor_id=instructor_id,
            group_id=checked_group,
            group_label=label,
            original_filename=checked_name,
        )
        async with session_factory() as session:
            result = await _run_with_saved_source(
                session,
                checked,
                checked_run,
                _extraction(CH1, CH2),
                settings,
                instructor_id=instructor_id,
            )
        rows = await _flags(session_factory, result.id)
        kinds = {r.detail["kind"] for r in rows}
        assert "reuse_same_instructor_resubmission" not in kinds, library_name
        assert rows and all(r.severity in {"high", "med"} for r in rows), library_name
        assert result.detail["n_same_work_sources"] == 0

    # Another instructor's upload with the same name and text is not this
    # instructor's own work: it stays a lead.
    other_record, _ = await _seed_manuscript_and_run(session_factory, group_label="Elsewhere")
    async with session_factory() as session:
        await session.execute(
            text("UPDATE manuscript SET original_filename = 'shared.pdf' WHERE id = :id"),
            {"id": other_record},
        )
        await session.execute(
            text(
                "TRUNCATE library_entry, manuscript_archive, manuscript_chapter_archive, "
                "manuscript_passage_archive"
            )
        )
        await session.commit()
    await _add_to_library(session_factory, other_record, _extraction(CH1, CH2))
    checked, checked_run = await _seed_upload(
        session_factory,
        instructor_id=instructor_id,
        group_id=None,
        original_filename="shared.pdf",
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session,
            checked,
            checked_run,
            _extraction(CH1, CH2),
            get_settings(),
            instructor_id=instructor_id,
        )
    rows = await _flags(session_factory, result.id)
    assert rows and all(r.severity == "high" for r in rows)
    assert {r.detail["matched_manuscript_id"] for r in rows} == {other_record}


async def test_a_shared_file_name_alone_does_not_make_dissimilar_text_the_same_work(
    session_factory,
):
    """The name rule needs the whole text to be similar too: a new project
    saved under last year's file name is not another upload of it."""
    instructor_id = await _seed_instructor(session_factory)
    record, _ = await _seed_upload(
        session_factory,
        instructor_id=instructor_id,
        group_id=None,
        original_filename="clinic-queue.pdf",
    )
    await _add_to_library(
        session_factory,
        record,
        _extraction(
            "An entirely unrelated introduction about renewable energy policy.",
            "An entirely unrelated methodology about survey sampling.",
        ),
    )
    checked, checked_run = await _seed_upload(
        session_factory,
        instructor_id=instructor_id,
        group_id=None,
        original_filename="clinic-queue.pdf",
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session,
            checked,
            checked_run,
            _extraction(CH1, CH2),
            get_settings(),
            instructor_id=instructor_id,
        )
    assert await _flags(session_factory, result.id) == []
    assert result.detail["n_same_work_sources"] == 0
    assert result.detail["archive_size_n"] == 1


def _this_term() -> str:
    now = datetime.now(UTC)
    start = now.year if now.month >= get_settings().reuse_school_year_start_month else now.year - 1
    return f"{start}-{start + 1}"


_THIS_TERM = _this_term()


def _record(
    *,
    team: str = DEFAULT_GROUP_LABEL,
    file: str | None = None,
    title: str | None = None,
    entry_team: str | None = None,
    authors: list[str] | None = None,
    members: list[str] | None = None,
    school_year: str | None = None,
) -> dict:
    return {
        "team": team,
        "file": file,
        "title": title,
        "entry_team": entry_team,
        "authors": authors or [],
        "members": members or [],
        "school_year": school_year,
    }


async def _upload_as(session_factory, instructor_id: int, record: dict) -> tuple[int, int]:
    """One upload under its group, with the names read from its title page
    (group members), the way ingestion records them."""
    async with session_factory() as session:
        group_id = await session.scalar(
            text(
                "SELECT id FROM manuscript_group WHERE instructor_id = :i AND name_normalized = :n"
            ),
            {"i": instructor_id, "n": normalize_group_name(record["team"])},
        )
    if group_id is None:
        group_id = await _make_group(
            session_factory, instructor_id=instructor_id, name=record["team"]
        )
    if record["members"]:
        async with session_factory() as session:
            session.add_all(GroupMember(group_id=group_id, name=n) for n in record["members"])
            await session.commit()
    manuscript_id, run_id = await _seed_upload(
        session_factory,
        instructor_id=instructor_id,
        group_id=group_id,
        group_label=record["team"],
        original_filename=record["file"],
    )
    return manuscript_id, run_id


async def _record_names(session_factory, manuscript_id: int, record: dict):
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE library_entry SET title = :title, team_name = :team, "
                "authors = CAST(:authors AS jsonb), school_year = :year WHERE manuscript_id = :id"
            ),
            {
                "title": record["title"],
                "team": record["entry_team"],
                "authors": json.dumps(record["authors"]),
                "year": record["school_year"],
                "id": manuscript_id,
            },
        )
        await session.commit()


async def _check_against_one_record(session_factory, own: dict, other: dict, settings):
    """Library holds `other` (same instructor, same text); `own` is checked.
    The checked side's own title or authors, when given, sit on a removed
    Library entry, as on a legacy upload that left the Library."""
    instructor_id = await _seed_instructor(session_factory)
    other_id, _ = await _upload_as(session_factory, instructor_id, other)
    await _add_to_library(session_factory, other_id, _extraction(CH1, CH2))
    await _record_names(session_factory, other_id, other)
    own_id, own_run = await _upload_as(session_factory, instructor_id, own)
    if own["title"] or own["authors"] or own["entry_team"]:
        async with session_factory() as session:
            session.add(
                LibraryEntry(
                    manuscript_id=own_id,
                    source=LibraryEntrySource.library_submission,
                    status=LibraryEntryStatus.removed,
                )
            )
            await session.commit()
        await _record_names(session_factory, own_id, own)
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session,
            own_id,
            own_run,
            _extraction(CH1, CH2),
            settings,
            instructor_id=instructor_id,
        )
    return other_id, result, await _flags(session_factory, result.id)


@pytest.mark.parametrize(
    "own,other,basis",
    [
        (
            _record(team="Ungrouped", title="Clinic Queue Notifier"),
            _record(team="Clinic Squad", title="clinic queue notifier"),
            "same_title",
        ),
        (
            _record(team="Clinic Squad", file="a.pdf"),
            _record(team="Ungrouped", entry_team="CLINIC  SQUAD", file="b.pdf"),
            "same_team",
        ),
        (
            _record(team="Ungrouped", authors=["Ana Cruz", "Ben Reyes"]),
            _record(team="Ungrouped", authors=["ben reyes", "ana  cruz"]),
            "same_authors",
        ),
        # The team's own earlier version, dated to this school year.
        (
            _record(team="Ungrouped", title="Clinic Queue Notifier"),
            _record(team="Clinic Squad", title="Clinic Queue Notifier", school_year=_THIS_TERM),
            "same_title",
        ),
        # An ordinary check upload has no Library entry; the names read from
        # its title page stand in for its authors.
        (
            _record(team="Byte Builders", members=["Ana Cruz", "Ben Reyes"]),
            _record(team="Ungrouped", authors=["Ben Reyes", "Ana Cruz"]),
            "same_authors",
        ),
    ],
)
async def test_a_recorded_title_team_or_author_list_identifies_the_same_work(
    session_factory, own, other, basis
):
    _, result, rows = await _check_against_one_record(
        session_factory, own, other, _no_near_exact_rule()
    )
    assert [(r.severity, r.detail["kind"]) for r in rows] == [
        ("low", "reuse_same_instructor_resubmission")
    ]
    assert rows[0].detail["same_work"][0]["basis"] == basis


@pytest.mark.parametrize(
    "own,other",
    [
        # A different team's sequel: the titles differ, so no name matches.
        (
            _record(team="Queue A", title="Smart Queue Management System 2"),
            _record(team="Queue B", title="Smart Queue Management System"),
        ),
        # Section and group codes say nothing about which work it is.
        (_record(team="IT41S2 G3"), _record(team="IT41S2-G3")),
        (
            _record(team="Ungrouped", file="BSIT_Capstone_Manuscript.pdf"),
            _record(team="Ungrouped", file="BSIT Capstone Manuscript (2).pdf"),
        ),
        # The same file name, but the records name different teams.
        (
            _record(team="Team Alpha", file="queue.pdf"),
            _record(team="Ungrouped", entry_team="Team Beta", file="queue.pdf"),
        ),
        # The same file name, but no author in common (title-page names on
        # the checked side).
        (
            _record(team="Byte Builders", members=["Ana Cruz"], file="queue.pdf"),
            _record(team="Ungrouped", authors=["Carla Diaz"], file="queue.pdf"),
        ),
        # Titles that share only generic words disagree.
        (
            _record(team="Ungrouped", title="Online Enrollment System", file="ols.pdf"),
            _record(team="Ungrouped", title="Online Attendance System", file="ols.pdf"),
        ),
        # Titles that share one identifying word of five still disagree.
        (
            _record(team="Ungrouped", title="Clinic Queue Notifier", file="clinic.pdf"),
            _record(team="Ungrouped", title="Clinic Payroll Tracker", file="clinic.pdf"),
        ),
        # A dated past-term submission with the same title is another cohort's.
        (
            _record(team="IT41S2-G4", title="Clinic Queue Notifier"),
            _record(team="Ungrouped", title="Clinic Queue Notifier", school_year="2023-2024"),
        ),
    ],
)
async def test_a_name_that_says_nothing_or_records_that_disagree_keep_the_leads(
    session_factory, own, other
):
    other_id, result, rows = await _check_against_one_record(
        session_factory, own, other, _no_near_exact_rule()
    )
    assert rows
    assert all(r.detail["kind"] != "reuse_same_instructor_resubmission" for r in rows)
    assert all(r.severity in {"high", "med"} for r in rows)
    assert {r.detail["matched_manuscript_id"] for r in rows} == {other_id}
    assert result.detail["n_same_work_sources"] == 0


@pytest.mark.parametrize(
    "own,other",
    [
        # The team renamed itself and its title after the proposal defense.
        (
            _record(team="VERIDICAL", title="Defense Readiness Platform"),
            _record(team="Team Lambda", title="Automated Integrity Checker"),
        ),
        # Or a new team handed in an earlier cohort's thesis.
        (
            _record(team="Ungrouped", authors=["Ana Cruz"]),
            _record(team="Ungrouped", authors=["Carla Diaz"]),
        ),
    ],
)
async def test_near_identical_text_with_disagreeing_records_is_one_lead_to_confirm(
    session_factory, own, other
):
    """BUG-140 collapses a near-exact duplicate from the same instructor. When
    the records disagree the system cannot tell a renamed team from a copied
    past thesis, so it says so in one lead the instructor confirms, instead
    of either hiding it or raising every passage."""
    other_id, result, rows = await _check_against_one_record(
        session_factory, own, other, get_settings()
    )
    assert [(r.severity, r.detail["kind"]) for r in rows] == [
        ("med", "reuse_same_instructor_resubmission")
    ]
    detail = rows[0].detail
    assert detail["same_work"][0]["basis"] == "near_identical_text_names_differ"
    assert detail["matched_manuscript_ids"] == [other_id]
    assert (
        "Please confirm whether it is this manuscript or another team's work" in (detail["reason"])
    )
    assert exclusion_reason("originality_reuse", detail) is not None


async def test_the_same_file_is_the_same_work_even_when_its_records_disagree(session_factory):
    instructor_id = await _seed_instructor(session_factory)
    record, _ = await _upload_as(
        session_factory, instructor_id, _record(team="Team Beta", file="x.pdf")
    )
    async with session_factory() as session:
        await session.execute(
            text("UPDATE manuscript SET content_hash = :h WHERE id = :id"),
            {"h": "d" * 64, "id": record},
        )
        await session.commit()
    await _add_to_library(session_factory, record, _extraction(CH1, CH2))
    checked, checked_run = await _upload_as(
        session_factory, instructor_id, _record(team="Team Alpha", file="y.pdf")
    )
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session,
            checked,
            checked_run,
            _extraction(CH1, CH2),
            get_settings(),
            instructor_id=instructor_id,
            content_hash="d" * 64,
        )
        audit = await session.scalar(
            text(
                "SELECT payload FROM audit_log WHERE event_type = "
                "'originality_reuse_check_computed' AND check_run_id = :id"
            ),
            {"id": checked_run},
        )
    rows = await _flags(session_factory, result.id)
    assert [r.detail["kind"] for r in rows] == ["reuse_same_instructor_resubmission"]
    assert audit["same_work"] == [
        {"manuscript_id": record, "basis": "same_file", "similarity": pytest.approx(1.0)}
    ]
    assert audit["same_work_excluded_ids"] == [record]
    assert audit["same_work_name_rules"]["file"] == "packaged"
    assert len(audit["same_work_name_rules"]["sha256"]) == 64


async def test_a_quotation_with_its_citation_is_counted_not_raised(session_factory):
    """BUG-632: a Library passage quoted in quotation marks with a citation
    beside it raises nothing and is counted apart; the same words copied
    without quotation marks are raised."""
    settings = get_settings().model_copy(update={"reuse_passage_high_similarity_threshold": 0.5})
    source_text = (
        "The clinic queue prototype sent each patient a text message when their turn was "
        "near, and the nurses reported that the waiting room was calmer during the trial "
        "weeks than in the month before it started at the barangay health centre."
    )
    own_words = (
        "Our study instead asks how students choose an elective when two sections clash "
        "in the timetable."
    )
    other_chapter = "An unrelated chapter on deployment to the campus network."
    source_id, _ = await _seed_manuscript_and_run(session_factory, group_label="Quoted source")
    await _add_to_library(session_factory, source_id, _extraction(source_text, CH2))

    results = {}
    for name, chapter in (
        ("quoted", f'Garcia (2023) reported that "{source_text}" (p. 14). {own_words}'),
        ("unquoted", f"{source_text} {own_words}"),
    ):
        own_id, own_run = await _seed_manuscript_and_run(session_factory, group_label=name)
        async with session_factory() as session:
            results[name] = await _run_with_saved_source(
                session, own_id, own_run, _extraction(chapter, other_chapter), settings
            )
    assert await _flag_kinds(session_factory, results["quoted"].id) == []
    assert results["quoted"].detail["n_shared_wording_only_in_cited_quotations"] >= 1
    assert results["quoted"].detail["n_similar_without_shared_wording"] == 0
    unquoted_kinds = await _flag_kinds(session_factory, results["unquoted"].id)
    assert any(kind.endswith("_passage") for kind in unquoted_kinds)
    await _assert_counts_match_flags(session_factory, results["quoted"])


async def test_a_shared_standard_text_is_counted_and_named_not_raised(session_factory, tmp_path):
    """BUG-637: two theses carrying the same listed standard text (here a
    scale legend table) raise nothing; the result counts the pair and names
    the standard. Copied prose next to it is still raised."""
    from tests.test_reuse_standard_texts import LIKERT, _list_file

    settings = get_settings().model_copy(
        update={
            "reuse_passage_high_similarity_threshold": 0.5,
            "reuse_public_standard_texts_file": _list_file(tmp_path, {"Scale legend": LIKERT}),
        }
    )
    copied = (
        "Students who registered online spent less time waiting at the registrar, and the "
        "staff said the queue was shorter during enrolment week than in the previous term."
    )
    other_chapter = "An unrelated chapter on deployment to the campus network."
    source_id, _ = await _seed_manuscript_and_run(session_factory, group_label="Legend source")
    await _add_to_library(
        session_factory, source_id, _extraction(f"Table 2. {LIKERT}. {copied}", CH2)
    )
    results = {}
    for name, chapter in (
        ("legend", f"The respondents answered on this scale. {LIKERT}."),
        ("legend and copy", f"The respondents answered on this scale. {LIKERT}. {copied}"),
        (
            "legend and quoted copy",
            f'The respondents answered on this scale. {LIKERT}. Reyes (2019) wrote "{copied}" '
            "(p. 4).",
        ),
    ):
        own_id, own_run = await _seed_manuscript_and_run(session_factory, group_label=name)
        async with session_factory() as session:
            results[name] = await _run_with_saved_source(
                session, own_id, own_run, _extraction(chapter, other_chapter), settings
            )
    # Only leaving out both the legend and the cited quotation stops this
    # pair, so it is in both counts.
    both = results["legend and quoted copy"]
    assert await _flag_kinds(session_factory, both.id) == []
    assert both.detail["n_shared_wording_only_in_standard_texts"] >= 1
    assert both.detail["n_shared_wording_only_in_cited_quotations"] >= 1
    legend = results["legend"]
    assert await _flag_kinds(session_factory, legend.id) == []
    assert legend.detail["n_shared_wording_only_in_standard_texts"] >= 1
    assert legend.detail["standard_texts_set_aside"] == ["Scale legend"]
    async with session_factory() as session:
        details = (
            await session.scalars(
                text("SELECT detail FROM flag WHERE check_result_id = :id"),
                {"id": results["legend and copy"].id},
            )
        ).all()
    passage = [d for d in details if d["kind"].endswith("_passage")]
    section = [d for d in details if not d["kind"].endswith("_passage")]
    assert passage and section
    # The raised findings say which standard was not counted, by name only.
    for detail in passage + section:
        assert detail["standard_wording_left_out"] == ["Scale legend"]


async def test_a_deployment_standard_list_reaches_the_check_and_its_audit(
    session_factory, tmp_path
):
    """BUG-637: a standard text only the deployment list carries is left out
    of the comparison, and the audit says the deployment list was used."""
    from tests.test_reuse_standard_texts import LIKERT, MADE_UP_STANDARD, _list_file

    settings = get_settings().model_copy(
        update={
            "reuse_passage_high_similarity_threshold": 0.5,
            "reuse_public_standard_texts_file": _list_file(tmp_path / "pub", {"Legend": LIKERT}),
            "reuse_standard_texts_file": _list_file(
                tmp_path / "dep", {"Deployment-only definition": MADE_UP_STANDARD}
            ),
        }
    )
    other_chapter = "An unrelated chapter on deployment to the campus network."
    source_id, _ = await _seed_manuscript_and_run(session_factory, group_label="Definition A")
    await _add_to_library(
        session_factory, source_id, _extraction(f"We follow it: {MADE_UP_STANDARD}.", CH2)
    )
    own_id, own_run = await _seed_manuscript_and_run(session_factory, group_label="Definition B")
    async with session_factory() as session:
        result = await _run_with_saved_source(
            session,
            own_id,
            own_run,
            _extraction(f"This study adopts: {MADE_UP_STANDARD}.", other_chapter),
            settings,
        )
        payload = await session.scalar(
            text(
                "SELECT payload FROM audit_log WHERE check_run_id = :run "
                "AND event_type = 'originality_reuse_check_computed'"
            ),
            {"run": result.check_run_id},
        )
    assert await _flag_kinds(session_factory, result.id) == []
    assert result.detail["standard_texts_set_aside"] == ["Deployment-only definition"]
    lists = payload["standard_text_lists"]
    assert (lists["public_entries"], lists["deployment_entries"]) == (1, 1)
    assert lists["deployment"] == "loaded" and len(lists["deployment_sha256"]) == 12
