"""V-077 (D-028): the corpus is the Library. Live Postgres coverage for the
pieces outside the HTTP surface: byte-identical Library copies collapse to
one matched source, a purge takes the entry out of the Library, a cancelled
check can never withdraw a Library entry's comparison data, and migration
0037 carries over exactly the pre-D-028 comparison sources.
"""

import asyncio
import os
from datetime import UTC, datetime

import asyncpg
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.archive.service import purge_manuscript, withdraw_manuscript_if_orphaned
from app.checks.reuse.embed import compute_document_embeddings, compute_passage_embeddings
from app.checks.reuse.query import canonical_library_copies
from app.checks.reuse.service import run_originality_reuse_check
from app.checks.reuse.store import store_document_embeddings, store_passage_embeddings
from app.config import get_settings
from app.db import sqlalchemy_url
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock
from app.ingest.service import _write_raw_store, raw_store_path
from app.models.enums import LibraryEntrySource, LibraryEntryStatus
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

SCRATCH_DB = "veridical_library_corpus_test"
BODY_1 = (
    "The current process for tracking attendance is entirely manual, requiring "
    "instructors to pass around a physical sheet each session. This creates delays "
    "and frequent errors in record-keeping."
)
BODY_2 = (
    "We used a hybrid rule-based and AI approach to grading capstone manuscripts, "
    "combining deterministic checks with confidence-based escalation to the "
    "instructor."
)


@pytest.fixture(autouse=True)
def _private_source_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "comparison-data"))
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def scratch_url():
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
                "manuscript, manuscript_group, group_member, rubric, instructor "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


def _extraction() -> ExtractionResult:
    blocks = [
        TextBlock(text="Chapter 1: Introduction", page=1, max_font_size=11.0, bold_ratio=0.0),
        TextBlock(text=BODY_1, page=1, max_font_size=11.0, bold_ratio=0.0),
        TextBlock(text="Chapter 2: Methodology", page=5, max_font_size=11.0, bold_ratio=0.0),
        TextBlock(text=BODY_2, page=5, max_font_size=11.0, bold_ratio=0.0),
    ]
    return ExtractionResult(
        page_count=5,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        section_tree=SectionTree(
            source="heuristics",
            nodes=[
                SectionNode(title="Chapter 1: Introduction", level=1, page=1),
                SectionNode(title="Chapter 2: Methodology", level=1, page=5),
            ],
        ),
        blocks=blocks,
        images=[],
    )


_counter = 0


async def _instructor(session_factory) -> int:
    global _counter
    _counter += 1
    async with session_factory() as session:
        instructor = Instructor(email=f"corpus-{_counter}@test.local", display_name="Corpus")
        session.add(instructor)
        await session.commit()
        return instructor.id


async def _manuscript(session_factory, instructor_id: int, *, content_hash: str | None) -> int:
    async with session_factory() as session:
        manuscript = Manuscript(
            instructor_id=instructor_id,
            group_label="Ungrouped",
            file_ref="",
            content_hash=content_hash,
        )
        session.add(manuscript)
        await session.commit()
        return manuscript.id


async def _library_entry(session_factory, manuscript_id: int) -> None:
    settings = get_settings()
    extraction = _extraction()
    _write_raw_store(extraction, raw_store_path(settings, manuscript_id))
    async with session_factory() as session:
        await store_document_embeddings(
            session, manuscript_id, compute_document_embeddings(extraction, settings)
        )
        await store_passage_embeddings(
            session, manuscript_id, compute_passage_embeddings(extraction, settings), settings
        )
        session.add(
            LibraryEntry(
                manuscript_id=manuscript_id,
                source=LibraryEntrySource.library_submission,
                status=LibraryEntryStatus.active,
                added_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def _check_run(session_factory, manuscript_id: int, instructor_id: int) -> int:
    async with session_factory() as session:
        rubric = Rubric(instructor_id=instructor_id, title="Format", source_file="r.pdf")
        session.add(rubric)
        await session.commit()
        run = CheckRun(manuscript_id=manuscript_id, rubric_id=rubric.id)
        session.add(run)
        await session.commit()
        return run.id


async def _corpus_rows(session_factory, manuscript_id: int) -> int:
    async with session_factory() as session:
        return await session.scalar(
            text(
                "SELECT (SELECT count(*) FROM manuscript_archive WHERE manuscript_id = :id)"
                " + (SELECT count(*) FROM manuscript_chapter_archive WHERE manuscript_id = :id)"
                " + (SELECT count(*) FROM manuscript_passage_archive WHERE manuscript_id = :id)"
            ),
            {"id": manuscript_id},
        )


async def test_byte_identical_library_copies_are_reported_as_one_source(session_factory):
    """Two instructors add the same file (BUG-234 keeps either from being
    told about the other). A third instructor's check must not see one source
    as two: every finding points at the same canonical record."""
    shared_hash = "c" * 64
    first_id = await _manuscript(
        session_factory, await _instructor(session_factory), content_hash=shared_hash
    )
    second_id = await _manuscript(
        session_factory, await _instructor(session_factory), content_hash=shared_hash
    )
    await _library_entry(session_factory, first_id)
    await _library_entry(session_factory, second_id)

    checker = await _instructor(session_factory)
    checked_id = await _manuscript(session_factory, checker, content_hash=None)
    _write_raw_store(_extraction(), raw_store_path(get_settings(), checked_id))
    run_id = await _check_run(session_factory, checked_id, checker)
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session,
            checked_id,
            run_id,
            _extraction(),
            get_settings(),
            instructor_id=checker,
            content_hash=None,
        )
        refs = (
            await session.scalars(
                text(
                    "SELECT DISTINCT (detail->>'matched_manuscript_id')::bigint FROM flag "
                    "WHERE check_result_id = :id"
                ),
                {"id": result.id},
            )
        ).all()
    assert result.detail["n_flags"] > 0
    assert refs == [first_id]
    # The end-to-end result alone can pass by luck (the vector index may
    # break the tie toward the lower id anyway), so the mapping itself is
    # asserted directly: the higher-id copy is redirected, never the reverse.
    async with session_factory() as session:
        remapped = await canonical_library_copies(
            session, {first_id, second_id}, checker, exclude_manuscript_ids={checked_id}
        )
    assert set(remapped) == {second_id}
    assert remapped[second_id][0] == first_id


async def test_the_requesters_own_identical_copy_is_preferred(session_factory):
    """Canonical choice prefers the checking instructor's own copy, so the
    BUG-140 same-instructor resubmission rule still recognizes it even when
    another account holds a lower-id identical copy."""
    shared_hash = "d" * 64
    other_id = await _manuscript(
        session_factory, await _instructor(session_factory), content_hash=shared_hash
    )
    checker = await _instructor(session_factory)
    own_id = await _manuscript(session_factory, checker, content_hash=shared_hash)
    await _library_entry(session_factory, other_id)
    await _library_entry(session_factory, own_id)

    checked_id = await _manuscript(session_factory, checker, content_hash=None)
    _write_raw_store(_extraction(), raw_store_path(get_settings(), checked_id))
    run_id = await _check_run(session_factory, checked_id, checker)
    async with session_factory() as session:
        result = await run_originality_reuse_check(
            session,
            checked_id,
            run_id,
            _extraction(),
            get_settings(),
            instructor_id=checker,
            content_hash=None,
        )
        rows = (
            await session.execute(
                text(
                    "SELECT detail->>'kind' AS kind, detail->>'matched_manuscript_id' AS ref "
                    "FROM flag WHERE check_result_id = :id"
                ),
                {"id": result.id},
            )
        ).all()
    assert [(r.kind, int(r.ref)) for r in rows] == [("reuse_same_instructor_resubmission", own_id)]


async def test_purge_takes_the_manuscript_out_of_the_library(
    session_factory, tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    owner = await _instructor(session_factory)
    manuscript_id = await _manuscript(session_factory, owner, content_hash=None)
    await _library_entry(session_factory, manuscript_id)

    async with session_factory() as session:
        from app.models.enums import IngestStatus

        # This control represents an already prepared active source. Pending
        # uploads are now explicitly protected against deletion while reading.
        manuscript = await session.get(Manuscript, manuscript_id)
        manuscript.ingest_status = IngestStatus.done
        await session.commit()
        await purge_manuscript(session, owner, manuscript_id, get_settings())
        row = (
            await session.execute(
                text("SELECT status, removal_reason FROM library_entry WHERE manuscript_id = :id"),
                {"id": manuscript_id},
            )
        ).one()
    assert (row.status, row.removal_reason) == ("removed", "purged")
    assert await _corpus_rows(session_factory, manuscript_id) == 0
    get_settings.cache_clear()


async def test_a_cancelled_check_never_withdraws_a_library_entrys_comparison_data(
    session_factory,
):
    """BUG-178's cleanup removed corpus rows a cancelled run might have
    written. Since V-077 a check writes none, so rows on an active Library
    entry always belong to the Library and must survive."""
    owner = await _instructor(session_factory)
    manuscript_id = await _manuscript(session_factory, owner, content_hash=None)
    await _library_entry(session_factory, manuscript_id)
    run_id = await _check_run(session_factory, manuscript_id, owner)

    async with session_factory() as session:
        withdrawn = await withdraw_manuscript_if_orphaned(
            session, manuscript_id=manuscript_id, check_run_id=run_id, reached_integrity=True
        )
    assert withdrawn is False
    assert await _corpus_rows(session_factory, manuscript_id) > 0


def test_migration_0037_carries_over_exactly_the_existing_comparison_sources():
    """Built at revision 0036 with three kinds of pre-D-028 manuscript, then
    upgraded: only the non-purged one with comparison data becomes an active
    legacy entry, with its group's title, members and program snapshotted."""
    from alembic import command
    from tests.test_schema import _admin_execute, _alembic_config, _swap_db

    base = os.environ["DATABASE_URL"]
    db_name = "veridical_library_migration_test"
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{db_name}"'))
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{db_name}"'))
    url = _swap_db(base, db_name)
    try:
        command.upgrade(_alembic_config(url), "0036")
        asyncio.run(_seed_pre_library(url))
        command.upgrade(_alembic_config(url), "0037")
        rows = asyncio.run(_fetch(url, "SELECT * FROM library_entry ORDER BY id"))
        it_id = asyncio.run(_fetch(url, "SELECT id FROM program WHERE name = 'IT'"))[0]["id"]
        origins = asyncio.run(_fetch(url, "SELECT DISTINCT origin FROM manuscript"))
    finally:
        asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))

    assert [r["origin"] for r in origins] == ["check_upload"]
    assert len(rows) == 1
    entry = rows[0]
    assert entry["manuscript_id"] == 1
    assert entry["source"] == "legacy_check_upload"
    assert entry["status"] == "active"
    assert entry["title"] == "A Real Capstone Title"
    assert entry["team_name"] == "Team Alpha"
    assert entry["authors"] == '["Ana Cruz", "Ben Reyes"]'
    assert entry["program_id"] == it_id
    assert entry["added_at"] is not None


async def _seed_pre_library(url: str) -> None:
    vector = "[" + ",".join(["0.1"] * 256) + "]"
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(
            "INSERT INTO instructor (email, display_name) VALUES ('legacy@test.local', 'Legacy')"
        )
        # Migration 0025 already seeded the programs.
        await conn.execute(
            "INSERT INTO manuscript_group (instructor_id, name, name_normalized, program_id, title)"
            " VALUES (1, 'Team Alpha', 'team alpha', (SELECT id FROM program WHERE name = 'IT'),"
            " 'A Real Capstone Title')"
        )
        await conn.execute(
            "INSERT INTO group_member (group_id, name) VALUES (1, 'Ana Cruz'), (1, 'Ben Reyes')"
        )
        # 1: checked, has comparison data -> carried over.
        # 2: uploaded, never finished a check (no comparison data) -> not carried.
        # 3: had comparison data once but was purged (purge deletes it) -> not carried.
        for purged in ("NULL", "NULL", "now()"):
            await conn.execute(
                "INSERT INTO manuscript (instructor_id, file_ref, group_label, group_id, purged_at)"
                f" VALUES (1, 'x.pdf', 'Team Alpha', 1, {purged})"
            )
        await conn.execute(
            "INSERT INTO manuscript_archive (manuscript_id, embedding, model_id)"
            f" VALUES (1, '{vector}', 'm')"
        )
    finally:
        await conn.close()


async def _fetch(url: str, sql: str) -> list[dict]:
    conn = await asyncpg.connect(url)
    try:
        return [dict(r) for r in await conn.fetch(sql)]
    finally:
        await conn.close()


async def test_duplicate_cleanup_previews_then_collapses_only_certain_copies(session_factory):
    """Owner's rule: keep the existing records, remove duplicates. Exact
    copies (same bytes) are certain and collapse to the newest; same text in
    a different file is a judgment, listed but untouched until named; corpus
    rows belonging to no active entry are removed."""
    from app.maintenance.library_duplicates import apply_cleanup, preview_library_duplicates

    settings = get_settings()
    owner = await _instructor(session_factory)
    older_copy = await _manuscript(session_factory, owner, content_hash="e" * 64)
    newer_copy = await _manuscript(session_factory, owner, content_hash="e" * 64)
    same_text_other_file = await _manuscript(session_factory, owner, content_hash="f" * 64)
    for manuscript_id in (older_copy, newer_copy, same_text_other_file):
        await _library_entry(session_factory, manuscript_id)
    other_owner = await _instructor(session_factory)
    other_owners_copy = await _manuscript(session_factory, other_owner, content_hash="e" * 64)
    await _library_entry(session_factory, other_owners_copy)
    orphan = await _manuscript(session_factory, owner, content_hash=None)
    async with session_factory() as session:
        await store_document_embeddings(
            session, orphan, compute_document_embeddings(_extraction(), settings)
        )

    async with session_factory() as session:
        preview = await preview_library_duplicates(session, settings)
    assert preview.active_entries == 4
    # Each owner keeps their own copy; the cross-account copy is only listed.
    assert preview.cross_account_copy_groups == (
        tuple(sorted((older_copy, newer_copy, other_owners_copy))),
    )
    assert [(g.keep_manuscript_id, g.remove_manuscript_ids) for g in preview.exact_copy_groups] == [
        (newer_copy, (older_copy,))
    ]
    # Same text, different files: listed, never applied without a name.
    pair_ids = {(p.manuscript_id, p.other_manuscript_id) for p in preview.same_text_pairs}
    assert (newer_copy, same_text_other_file) in pair_ids
    assert preview.orphan_corpus_manuscript_ids == (orphan,)
    # Preview changed nothing.
    assert await _corpus_rows(session_factory, older_copy) > 0

    async with session_factory() as session:
        applied = await apply_cleanup(session, settings)
    assert applied.applied_removals == (older_copy,)
    assert await _corpus_rows(session_factory, older_copy) == 0
    assert await _corpus_rows(session_factory, orphan) == 0
    assert await _corpus_rows(session_factory, same_text_other_file) > 0

    async with session_factory() as session:
        named = await apply_cleanup(session, settings, remove_named=(same_text_other_file,))
        statuses = dict(
            (
                await session.execute(
                    text("SELECT manuscript_id, removal_reason FROM library_entry")
                )
            ).all()
        )
    assert named.applied_removals == (same_text_other_file,)
    assert statuses == {
        older_copy: "duplicate_cleanup",
        newer_copy: None,
        same_text_other_file: "duplicate_cleanup",
        other_owners_copy: None,
    }


async def test_an_entry_removed_while_its_request_reads_it_is_never_activated(
    session_factory, tmp_path, monkeypatch
):
    """The race both reviewers reproduced: a removal during reading used to be
    undone when the reading request activated the entry, putting removed text
    back into the shared corpus. Now the late activation is refused and the
    files the late work re-created are deleted."""
    from app.ingest.service import _write_raw_store, raw_store_path
    from app.library.service import _activate

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    settings = get_settings()
    owner = await _instructor(session_factory)
    manuscript_id = await _manuscript(session_factory, owner, content_hash=None)
    extraction = _extraction()
    async with session_factory() as session:
        session.add(
            LibraryEntry(
                manuscript_id=manuscript_id,
                source=LibraryEntrySource.library_submission,
                status=LibraryEntryStatus.processing,
            )
        )
        await session.commit()
    # The concurrent removal lands first; then the late work rewrites the
    # extraction the removal had already deleted.
    async with session_factory() as other:
        await other.execute(
            text(
                "UPDATE library_entry SET status = 'removed', removed_at = now() "
                "WHERE manuscript_id = :id"
            ),
            {"id": manuscript_id},
        )
        await other.execute(
            text("UPDATE manuscript SET purged_at = now() WHERE id = :id"), {"id": manuscript_id}
        )
        await other.commit()
    raw = raw_store_path(settings, manuscript_id)
    _write_raw_store(extraction, raw)

    async with session_factory() as session:
        entry = await session.scalar(
            select(LibraryEntry).where(LibraryEntry.manuscript_id == manuscript_id)
        )
        manuscript = await session.get(Manuscript, manuscript_id)
        activated = await _activate(
            session,
            entry,
            manuscript,
            compute_document_embeddings(extraction, settings),
            compute_passage_embeddings(extraction, settings),
            settings,
            expected=LibraryEntryStatus.processing,
            decision=None,
        )
        status = await session.scalar(
            text("SELECT status FROM library_entry WHERE manuscript_id = :id"),
            {"id": manuscript_id},
        )
    assert activated is False
    assert status == "removed"
    assert await _corpus_rows(session_factory, manuscript_id) == 0
    assert not raw.exists()
    get_settings.cache_clear()
