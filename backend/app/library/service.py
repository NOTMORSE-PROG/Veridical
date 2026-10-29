"""V-077 (D-028): the Library -- a standalone collection of manuscripts and
the only F7 comparison corpus.

**One invariant, owned here:** the F7 embedding tables
(`manuscript_archive`, `manuscript_chapter_archive`,
`manuscript_passage_archive`) hold rows for a manuscript exactly when its
`library_entry` is `active`. `_activate` writes them, `_remove` deletes them,
both under the same per-manuscript advisory lock the archive purge uses
(BUG-178). Every similarity query therefore searches the Library and nothing
else, with no membership filter to erode HNSW recall.

**Submission** (`submit_library_entry`) stores the file, extracts text with
image reading skipped, and embeds with the local model: zero Gemini calls.
No rubric, no grading, no verdict.

**Duplicates are own-account only.** An exact copy (same bytes as one of the
requester's own entries) creates nothing; a file whose text is very similar
to one of the requester's own entries (which may be the same manuscript in
another file, or a revised version) waits in `needs_decision` for the
instructor (ground rule 1). Neither check
ever correlates across accounts: BUG-234 ruled that telling one instructor
another account holds the same bytes is a cross-tenant existence oracle.
Cross-account copies are collapsed at match time instead
(`app.checks.reuse.query.canonical_library_copies`).

**Visibility.** Active entries are visible to every instructor (the corpus is
shared, BUG-050), redacted for other accounts exactly as BUG-147/D-026
require. Processing, needs-decision and failed entries are visible to their
owner only. Removed entries are visible to no one. V-079 makes active Library
documents readable in full by signed-in instructors; structured shared identity
fields remain redacted. Editing/removal and ordinary check sources stay private.
"""

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import messages
from app.archive.service import delete_archive_rows, delete_stored_files
from app.audit.service import write_audit_event
from app.checks.reuse.embed import (
    DocumentEmbeddings,
    LibraryPassageEmbeddings,
    PassageEmbedding,
    compute_document_embeddings,
    compute_library_passage_embeddings,
    split_context,
)
from app.checks.reuse.store import store_document_embeddings, store_passage_embeddings
from app.config import Settings, get_settings
from app.db import advisory_lock
from app.errors import ConflictError, GoneError, NotFoundError, VeridicalError
from app.groups.service import DEFAULT_GROUP_LABEL, UNSET_PROGRAM_FILTER
from app.ingest.admission import PreparationLease, preparation_scope
from app.ingest.analytical_source import bind_analytical_source
from app.ingest.patterns import load_patterns
from app.ingest.schemas import ExtractionResult
from app.ingest.service import (
    clean_original_filename,
    detect_format,
    hash_file_sha256,
    ingest_manuscript,
    load_raw_store_async,
    save_upload,
    select_extractor,
)
from app.ingest.titlepage import extract_title_page
from app.library.passage_indexing import indexing_snapshot, project_passage_indexing
from app.library.schemas import (
    LibraryChapterExcerptOut,
    LibraryDuplicateRefOut,
    LibraryEntryStateOut,
    LibraryEntryUpdate,
    LibraryExcerptOut,
    LibraryItemOut,
    LibrarySubmissionIn,
    LibrarySubmissionOut,
    LibrarySubmissionOutcome,
    PaginatedLibrary,
)
from app.models.enums import (
    LibraryEntrySource,
    LibraryEntryStatus,
    LibraryRemovalReason,
    ManuscriptOrigin,
)
from app.models.group import Program
from app.models.library import LibraryEntry
from app.models.manuscript import (
    Manuscript,
    ManuscriptArchive,
    ManuscriptChapterArchive,
    ManuscriptPassageArchive,
)
from app.report.docx_images import manuscript_docx_image_for
from app.report.schemas import DocumentParagraphsOut, ManuscriptViewerOut
from app.report.service import (
    manuscript_file_path_for,
    manuscript_paragraphs_for,
    manuscript_viewer_for,
)
from app.report.source_files import readable_source_file
from app.storage import get_storage, storage_key_for
from app.storage.access import lock_source, read_source

# Lengths come from the columns themselves, so a schema change cannot leave
# a silent truncation mismatch behind.
_TITLE_MAX = LibraryEntry.__table__.c.title.type.length
_TEAM_MAX = LibraryEntry.__table__.c.team_name.type.length
_AUTHOR_MAX = 200

logger = logging.getLogger(__name__)

LibraryListScope = Literal["active", "pending"]

_OWNER_ONLY_STATUSES = (
    LibraryEntryStatus.processing,
    LibraryEntryStatus.needs_decision,
    LibraryEntryStatus.failed,
)


def _stalled_cutoff() -> datetime:
    return datetime.now(UTC) - timedelta(seconds=get_settings().library_processing_stalled_seconds)


def _is_stalled(entry: LibraryEntry) -> bool:
    """A processing entry whose request died mid-way (crash, restart). It no
    longer blocks re-adding the same file and its owner may remove it."""
    return entry.status == LibraryEntryStatus.processing and entry.created_at < _stalled_cutoff()


def _record_label(manuscript_id: int) -> str:
    return f"Library record #{manuscript_id}"


def _visible_to(instructor_id: int):
    """Active entries for everyone; the owner also sees their own entries
    that are still processing, waiting for a decision, or failed."""
    return or_(
        LibraryEntry.status == LibraryEntryStatus.active,
        and_(
            Manuscript.instructor_id == instructor_id,
            LibraryEntry.status.in_(_OWNER_ONLY_STATUSES),
        ),
    )


def _own_label(entry: LibraryEntry, manuscript: Manuscript) -> str:
    return (
        entry.title
        or entry.team_name
        or manuscript.original_filename
        or _record_label(manuscript.id)
    )


def _duplicate_ref(entry: LibraryEntry, manuscript: Manuscript) -> LibraryDuplicateRefOut:
    return LibraryDuplicateRefOut(
        manuscript_id=manuscript.id,
        label=_own_label(entry, manuscript),
        original_filename=manuscript.original_filename,
        added_at=entry.added_at,
    )


def _item_out(
    entry: LibraryEntry,
    manuscript: Manuscript,
    program_name: str | None,
    *,
    requesting_instructor_id: int,
    duplicate_of: LibraryDuplicateRefOut | None = None,
    archived_model_id: str | None = None,
) -> LibraryItemOut:
    """BUG-147: another account's entry keeps only what the shared corpus
    needs to stay explainable (record number, program, school year, date
    added). Title, team, authors, filename, source, failure detail and
    duplicate links are never returned for an entry the requester doesn't
    own -- and never even passed in for one (callers resolve duplicates for
    own entries only)."""
    if manuscript.instructor_id != requesting_instructor_id:
        return LibraryItemOut(
            manuscript_id=manuscript.id,
            is_own=False,
            label=_record_label(manuscript.id),
            title=None,
            team_name=None,
            authors=[],
            program=program_name,
            school_year=entry.school_year,
            original_filename=None,
            status=entry.status,
            source=None,
            added_at=entry.added_at,
            created_at=entry.created_at,
            passage_indexing=project_passage_indexing(
                entry.passage_indexing_snapshot, manuscript.content_hash, archived_model_id
            ),
        )
    return LibraryItemOut(
        manuscript_id=manuscript.id,
        is_own=True,
        label=_own_label(entry, manuscript),
        title=entry.title,
        team_name=entry.team_name,
        authors=list(entry.authors or []),
        program=program_name,
        school_year=entry.school_year,
        original_filename=manuscript.original_filename,
        status=entry.status,
        source=entry.source,
        added_at=entry.added_at,
        created_at=entry.created_at,
        proposed_fields=list(entry.proposed_fields or []),
        possible_duplicate_of=(
            duplicate_of if entry.status == LibraryEntryStatus.needs_decision else None
        ),
        failure_detail=(
            entry.failure_detail if entry.status == LibraryEntryStatus.failed else None
        ),
        stalled=_is_stalled(entry),
        passage_indexing=project_passage_indexing(
            entry.passage_indexing_snapshot, manuscript.content_hash, archived_model_id
        ),
    )


def _entry_select():
    return (
        select(LibraryEntry, Manuscript, Program.name, ManuscriptArchive.model_id)
        .join(Manuscript, Manuscript.id == LibraryEntry.manuscript_id)
        .outerjoin(Program, Program.id == LibraryEntry.program_id)
        .outerjoin(ManuscriptArchive, ManuscriptArchive.manuscript_id == Manuscript.id)
    )


async def _duplicate_refs(
    session: AsyncSession, instructor_id: int, entries: list[LibraryEntry]
) -> dict[int, LibraryDuplicateRefOut]:
    """One query for every needs-decision entry on the page, restricted to
    the requester's own entries (BUG-234: never another account's)."""
    target_ids = {
        e.possible_duplicate_of_id
        for e in entries
        if e.status == LibraryEntryStatus.needs_decision and e.possible_duplicate_of_id
    }
    if not target_ids:
        return {}
    rows = (
        await session.execute(
            select(LibraryEntry, Manuscript)
            .join(Manuscript, Manuscript.id == LibraryEntry.manuscript_id)
            .where(
                LibraryEntry.id.in_(target_ids),
                Manuscript.instructor_id == instructor_id,
                LibraryEntry.status != LibraryEntryStatus.removed,
            )
        )
    ).all()
    return {entry.id: _duplicate_ref(entry, manuscript) for entry, manuscript in rows}


async def list_library(
    session: AsyncSession,
    instructor_id: int,
    *,
    page: int = 1,
    page_size: int = 50,
    program: str | None = None,
    search: str | None = None,
    scope: LibraryListScope = "active",
) -> PaginatedLibrary:
    """`scope="active"` is the collection itself: every active entry, own
    and shared (the count a screen shows as "records in the Library").
    `scope="pending"` is the requester's own uploads that are not
    comparison sources yet (processing, needs decision, failed), kept apart
    so the collection count never includes them.

    `search` matches title, team name, authors, filename or school year,
    and only ever over the requester's own entries (BUG-147: a hit on
    another account's student name would itself confirm that name exists
    in the corpus). `program` filters every listed entry."""
    if scope == "pending":
        filters = [
            Manuscript.instructor_id == instructor_id,
            LibraryEntry.status.in_(_OWNER_ONLY_STATUSES),
        ]
    else:
        filters = [LibraryEntry.status == LibraryEntryStatus.active]
    if program == UNSET_PROGRAM_FILTER:
        filters.append(LibraryEntry.program_id.is_(None))
    elif program is not None:
        filters.append(Program.name == program)
    record_number = search.strip().lstrip("#") if search else ""
    if record_number.isdigit():
        # "#20" or "20": a record number is shown on every card, own or
        # shared, and identifies no one, so it is searchable across the
        # whole listed scope (unlike names and titles, BUG-147).
        filters.append(Manuscript.id == int(record_number))
    elif search and search.strip():
        needle = f"%{search.strip()}%"
        filters.append(
            and_(
                Manuscript.instructor_id == instructor_id,
                or_(
                    LibraryEntry.title.ilike(needle),
                    LibraryEntry.team_name.ilike(needle),
                    LibraryEntry.school_year.ilike(needle),
                    cast(LibraryEntry.authors, Text).ilike(needle),
                    Manuscript.original_filename.ilike(needle),
                ),
            )
        )

    total = (
        await session.scalar(
            select(func.count())
            .select_from(LibraryEntry)
            .join(Manuscript, Manuscript.id == LibraryEntry.manuscript_id)
            .outerjoin(Program, Program.id == LibraryEntry.program_id)
            .where(*filters)
        )
    ) or 0
    rows = (
        await session.execute(
            _entry_select()
            .where(*filters)
            # `id` tie-break: rows created in one transaction share a
            # timestamp, and OFFSET pagination over a non-unique order is
            # non-deterministic (BUG-148 review finding).
            .order_by(
                func.coalesce(LibraryEntry.added_at, LibraryEntry.created_at).desc(),
                LibraryEntry.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    own_entries = [e for e, m, _, _model in rows if m.instructor_id == instructor_id]
    refs = await _duplicate_refs(session, instructor_id, own_entries)
    items = [
        _item_out(
            entry,
            manuscript,
            program_name,
            archived_model_id=archived_model_id,
            requesting_instructor_id=instructor_id,
            duplicate_of=(
                refs.get(entry.possible_duplicate_of_id)
                if manuscript.instructor_id == instructor_id
                else None
            ),
        )
        for entry, manuscript, program_name, archived_model_id in rows
    ]
    return PaginatedLibrary(items=items, total=total, page=page, page_size=page_size)


async def get_library_item(
    session: AsyncSession, instructor_id: int, manuscript_id: int
) -> LibraryItemOut:
    row = (
        await session.execute(
            _entry_select().where(
                LibraryEntry.manuscript_id == manuscript_id, _visible_to(instructor_id)
            )
        )
    ).first()
    if row is None:
        raise NotFoundError(f"No Library record with id {manuscript_id}.")
    entry, manuscript, program_name, archived_model_id = row
    duplicate_of = None
    if manuscript.instructor_id == instructor_id:
        duplicate_of = (await _duplicate_refs(session, instructor_id, [entry])).get(
            entry.possible_duplicate_of_id
        )
    return _item_out(
        entry,
        manuscript,
        program_name,
        requesting_instructor_id=instructor_id,
        duplicate_of=duplicate_of,
        archived_model_id=archived_model_id,
    )


async def _owned_entry(
    session: AsyncSession, instructor_id: int, manuscript_id: int
) -> tuple[LibraryEntry, Manuscript]:
    """Uniform 404 for "not yours", "not in the Library" and "removed"
    (this codebase's 404-never-403 convention)."""
    row = (
        await session.execute(
            select(LibraryEntry, Manuscript)
            .join(Manuscript, Manuscript.id == LibraryEntry.manuscript_id)
            .where(
                LibraryEntry.manuscript_id == manuscript_id,
                Manuscript.instructor_id == instructor_id,
                LibraryEntry.status != LibraryEntryStatus.removed,
            )
        )
    ).first()
    if row is None:
        raise NotFoundError(f"No Library record with id {manuscript_id}.")
    return row[0], row[1]


async def _program_id(session: AsyncSession, program: str | None) -> int | None:
    name = (program or "").strip()
    if not name or name == UNSET_PROGRAM_FILTER:
        return None
    program_id = await session.scalar(select(Program.id).where(Program.name == name))
    if program_id is None:
        raise NotFoundError(messages.LIBRARY_UNKNOWN_PROGRAM.format(program=name))
    return program_id


def _clean_text(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _clean_authors(authors: list[str] | None, settings: Settings) -> list[str]:
    cleaned = [a.strip()[:_AUTHOR_MAX] for a in (authors or []) if a and a.strip()]
    return cleaned[: settings.library_entry_max_authors]


def _fill_from_title_page(
    entry: LibraryEntry, result: ExtractionResult, settings: Settings
) -> None:
    """Deterministic V-063 extraction, no LLM call. Only fills fields the
    instructor left blank, and records which ones it filled."""
    proposal = extract_title_page(result, load_patterns(settings.ingest_patterns_file))
    proposed: list[str] = []
    if entry.title is None and proposal.title is not None:
        entry.title = proposal.title.value[:_TITLE_MAX]
        proposed.append("title")
    # A short name equal to the title is the extractor reading the same line
    # twice, not a team name (UX review, 2026-10-13).
    if (
        entry.team_name is None
        and proposal.short_name is not None
        and (proposal.title is None or proposal.short_name.value != proposal.title.value)
    ):
        entry.team_name = proposal.short_name.value[:_TEAM_MAX]
        proposed.append("team_name")
    if not entry.authors and proposal.members:
        entry.authors = _clean_authors([m.value for m in proposal.members], settings)
        if entry.authors:
            proposed.append("authors")
    entry.proposed_fields = proposed


async def _own_entry_with_hash(
    session: AsyncSession, instructor_id: int, content_hash: str
) -> tuple[LibraryEntry, Manuscript] | None:
    """Exact copy check over the requester's own entries only (BUG-234).
    Active, waiting, failed and live processing entries all count: re-sending
    bytes that already failed must not store another copy (security review,
    2026-10-13). A stalled processing entry does not count, so a crashed
    upload never blocks re-adding the file."""
    row = (
        await session.execute(
            select(LibraryEntry, Manuscript)
            .join(Manuscript, Manuscript.id == LibraryEntry.manuscript_id)
            .where(
                Manuscript.instructor_id == instructor_id,
                Manuscript.content_hash == content_hash,
                LibraryEntry.status != LibraryEntryStatus.removed,
                or_(
                    LibraryEntry.status != LibraryEntryStatus.processing,
                    LibraryEntry.created_at >= _stalled_cutoff(),
                ),
            )
            .order_by(LibraryEntry.id)
            .limit(1)
        )
    ).first()
    return (row[0], row[1]) if row is not None else None


_EXACT_COPY_MESSAGES = {
    LibraryEntryStatus.active: messages.LIBRARY_EXACT_DUPLICATE,
    LibraryEntryStatus.processing: messages.LIBRARY_EXACT_DUPLICATE_PROCESSING,
    LibraryEntryStatus.needs_decision: messages.LIBRARY_EXACT_DUPLICATE_WAITING,
}


async def _own_possible_duplicate(
    session: AsyncSession,
    instructor_id: int,
    manuscript_id: int,
    whole_document: list[float],
    settings: Settings,
) -> tuple[LibraryEntry, Manuscript, float] | None:
    """Same text, different file: whole-document cosine similarity at or
    above `reuse_exact_duplicate_threshold` against the requester's own
    active entries. Computed exactly in Python over the requester's own
    vectors (tens to hundreds), not through a filtered HNSW scan whose
    post-filtering could miss the nearest own entry."""
    rows = (
        await session.execute(
            select(LibraryEntry, Manuscript, ManuscriptArchive.embedding)
            .join(Manuscript, Manuscript.id == LibraryEntry.manuscript_id)
            .join(ManuscriptArchive, ManuscriptArchive.manuscript_id == Manuscript.id)
            .where(
                Manuscript.instructor_id == instructor_id,
                Manuscript.id != manuscript_id,
                LibraryEntry.status == LibraryEntryStatus.active,
                ManuscriptArchive.model_id == settings.embedding_model_id,
            )
        )
    ).all()
    if not rows:
        return None
    # Imported here so an idle server never loads numpy (BUG-532).
    import numpy as np

    query = np.asarray(whole_document, dtype=np.float64)
    query_norm = np.linalg.norm(query)
    if query_norm == 0:
        return None
    best: tuple[LibraryEntry, Manuscript, float] | None = None
    for entry, manuscript, embedding in rows:
        vector = np.asarray(embedding, dtype=np.float64)
        norm = np.linalg.norm(vector)
        if norm == 0:
            continue
        similarity = float(np.dot(query, vector) / (query_norm * norm))
        if similarity >= settings.reuse_exact_duplicate_threshold and (
            best is None or similarity > best[2]
        ):
            best = (entry, manuscript, similarity)
    return best


async def _embed(
    result: ExtractionResult,
    settings: Settings,
    *,
    preparation_lease: PreparationLease | None = None,
) -> tuple[DocumentEmbeddings, LibraryPassageEmbeddings]:
    """Local model2vec work, off the event loop (BUG-152)."""
    loop = asyncio.get_running_loop()
    document = (
        await preparation_lease.run(compute_document_embeddings, result, settings)
        if preparation_lease is not None
        else await loop.run_in_executor(None, compute_document_embeddings, result, settings)
    )
    passages = (
        await preparation_lease.run(compute_library_passage_embeddings, result, settings)
        if preparation_lease is not None
        else await loop.run_in_executor(None, compute_library_passage_embeddings, result, settings)
    )
    return document, passages


async def _discard_late_work(
    session: AsyncSession,
    entry: LibraryEntry,
    manuscript: Manuscript,
    settings: Settings,
    *,
    preparation_lease: PreparationLease | None = None,
) -> None:
    """The entry left the Library (removed, skipped, purged) while this
    request was still reading or embedding it. Delete only after a real
    removal: a competing activation can legitimately change the expected
    state, and its source and comparison data belong to the winning action.
    Ingestion may have rewritten the
    extraction (locally and durably) after the removal deleted it. Caller
    holds the manuscript's advisory lock."""
    await session.refresh(entry)
    await session.refresh(manuscript, with_for_update=True)
    if entry.status != LibraryEntryStatus.removed:
        return
    await delete_archive_rows(session, manuscript.id)
    if entry.source == LibraryEntrySource.library_submission:
        if preparation_lease is not None:
            await preparation_lease.run(delete_stored_files, manuscript, settings)
        else:
            await asyncio.get_running_loop().run_in_executor(
                None, delete_stored_files, manuscript, settings
            )
        if manuscript.purged_at is None:
            manuscript.purged_at = datetime.now(UTC)
    await session.commit()


async def _still(
    session: AsyncSession,
    entry: LibraryEntry,
    manuscript: Manuscript,
    expected: LibraryEntryStatus,
) -> bool:
    """Re-read the entry under its advisory lock (caller holds it): a user
    action on another request may have moved it since this request began."""
    await session.refresh(entry)
    await session.refresh(manuscript)
    return entry.status == expected and manuscript.purged_at is None


async def _activate(
    session: AsyncSession,
    entry: LibraryEntry,
    manuscript: Manuscript,
    document: DocumentEmbeddings,
    passages: list[PassageEmbedding],
    settings: Settings,
    *,
    expected: LibraryEntryStatus,
    decision: str | None,
    preparation_lease: PreparationLease | None = None,
    passage_inventory: LibraryPassageEmbeddings | None = None,
) -> bool:
    """Enter the Library: comparison rows and the status change commit in ONE
    transaction, so a crash can never leave rows for a non-active entry. Under
    the same lock `_remove` takes, and only if the entry is still in
    `expected`; otherwise the late work is discarded and False returned
    (security review / backend review, 2026-10-13: a removal during reading
    used to be undone by this write)."""
    async with advisory_lock(session, manuscript.id):
        if not await _still(session, entry, manuscript, expected):
            await _discard_late_work(
                session, entry, manuscript, settings, preparation_lease=preparation_lease
            )
            return False
        await store_document_embeddings(session, manuscript.id, document, commit=False)
        if passages:
            await store_passage_embeddings(session, manuscript.id, passages, settings, commit=False)
        entry.passage_indexing_snapshot = (
            indexing_snapshot(passage_inventory, manuscript.content_hash, document.model_id)
            if passage_inventory is not None
            else None
        )
        entry.status = LibraryEntryStatus.active
        entry.added_at = datetime.now(UTC)
        await write_audit_event(
            session,
            event_type="library_entry_added",
            check_run_id=None,
            manuscript_id=manuscript.id,
            payload={
                "source": entry.source.value,
                "decision": decision,
                "passages_stored": min(len(passages), settings.reuse_max_passages_per_manuscript),
                "passages_found": (
                    passage_inventory.passages_found
                    if passage_inventory is not None
                    else len(passages)
                ),
                "passages_embedded": len(passages),
                "passage_indexing": entry.passage_indexing_snapshot,
            },
        )
        await session.commit()
        return True


def _removed_while_reading() -> LibrarySubmissionOut:
    return LibrarySubmissionOut(
        outcome=LibrarySubmissionOutcome.failed,
        item=None,
        duplicate_of=None,
        message=messages.LIBRARY_REMOVED_WHILE_READING,
    )


async def _fail(
    session: AsyncSession,
    entry: LibraryEntry,
    manuscript: Manuscript,
    detail: str,
    settings: Settings,
    *,
    preparation_lease: PreparationLease | None = None,
) -> LibrarySubmissionOut:
    async with advisory_lock(session, manuscript.id):
        if not await _still(session, entry, manuscript, LibraryEntryStatus.processing):
            await _discard_late_work(
                session, entry, manuscript, settings, preparation_lease=preparation_lease
            )
            return _removed_while_reading()
        entry.status = LibraryEntryStatus.failed
        entry.failure_detail = detail
        await write_audit_event(
            session,
            event_type="library_entry_failed",
            check_run_id=None,
            manuscript_id=manuscript.id,
            payload={"source": entry.source.value},
        )
        await session.commit()
    return LibrarySubmissionOut(
        outcome=LibrarySubmissionOutcome.failed,
        item=await get_library_item(session, manuscript.instructor_id, manuscript.id),
        duplicate_of=None,
        message=detail,
    )


async def submit_library_entry(
    session: AsyncSession,
    chunks: AsyncIterator[bytes],
    filename: str,
    metadata: LibrarySubmissionIn,
    *,
    instructor_id: int,
    settings: Settings,
) -> LibrarySubmissionOut:
    """Admit before consuming this upload or creating a Library processing record."""
    with preparation_scope(settings) as lease:
        return await _submit_library_entry(
            session,
            chunks,
            filename,
            metadata,
            instructor_id=instructor_id,
            settings=settings,
            preparation_lease=lease,
        )


async def _submit_library_entry(
    session: AsyncSession,
    chunks: AsyncIterator[bytes],
    filename: str,
    metadata: LibrarySubmissionIn,
    *,
    instructor_id: int,
    settings: Settings,
    preparation_lease: PreparationLease,
) -> LibrarySubmissionOut:
    """One file per call; the client submits a batch one file at a time,
    while shared server admission independently bounds upload preparation owners.

    Nothing is stored durably or recorded until the content is a readable
    document type and is not a copy of one of the requester's own entries
    (security review, 2026-10-13: any file type used to reach durable
    storage, and identical failed retries each stored a new copy)."""
    program_id = await _program_id(session, metadata.program)
    suffix = Path(filename).suffix.lower()[:8]
    uploads = settings.data_dir / settings.manuscript_upload_subdir
    # Server-owned names only: the original filename never touches the
    # filesystem.
    pending = uploads / f"library-pending-{uuid.uuid4().hex}{suffix}"
    try:
        try:
            await save_upload(chunks, pending, settings)
            select_extractor(await preparation_lease.run(detect_format, pending))
        except VeridicalError as exc:
            return LibrarySubmissionOut(
                outcome=LibrarySubmissionOutcome.failed,
                item=None,
                duplicate_of=None,
                message=str(exc),
            )
        content_hash = await preparation_lease.run(
            hash_file_sha256, pending, settings.content_hash_read_chunk_bytes
        )

        existing = await _own_entry_with_hash(session, instructor_id, content_hash)
        if existing is not None:
            existing_entry, existing_manuscript = existing
            if existing_entry.status == LibraryEntryStatus.failed:
                return LibrarySubmissionOut(
                    outcome=LibrarySubmissionOutcome.failed,
                    item=await get_library_item(session, instructor_id, existing_manuscript.id),
                    duplicate_of=None,
                    message=messages.LIBRARY_EXACT_DUPLICATE_FAILED,
                )
            return LibrarySubmissionOut(
                outcome=LibrarySubmissionOutcome.exact_duplicate,
                item=None,
                duplicate_of=_duplicate_ref(existing_entry, existing_manuscript),
                message=_EXACT_COPY_MESSAGES[existing_entry.status],
            )

        team_name = _clean_text(metadata.team_name)
        manuscript = Manuscript(
            instructor_id=instructor_id,
            origin=ManuscriptOrigin.library_submission,
            group_id=None,
            group_label=team_name or DEFAULT_GROUP_LABEL,
            file_ref="",
            original_filename=clean_original_filename(filename),
            content_hash=content_hash,
        )
        session.add(manuscript)
        await session.flush()
        entry = LibraryEntry(
            manuscript_id=manuscript.id,
            source=LibraryEntrySource.library_submission,
            status=LibraryEntryStatus.processing,
            title=_clean_text(metadata.title),
            team_name=team_name,
            authors=_clean_authors(metadata.authors, settings),
            program_id=program_id,
            school_year=_clean_text(metadata.school_year),
            proposed_fields=[],
        )
        session.add(entry)
        await session.commit()

        dest = uploads / f"{manuscript.id}{suffix}"
        try:
            await preparation_lease.run(os.replace, pending, dest)
            # BUG-138: durable copy before the row claims the file exists.
            storage = get_storage(settings)
            await preparation_lease.run(
                storage.put_file, dest, storage_key_for(settings, str(dest))
            )
        except Exception:
            # Nothing references these rows yet: remove them entirely, so a
            # transient storage failure neither leaves a stuck entry nor
            # blocks re-adding the same file.
            logger.exception("Library submission %s could not be stored", manuscript.id)
            dest.unlink(missing_ok=True)
            await session.delete(entry)
            await session.delete(manuscript)
            await session.commit()
            return LibrarySubmissionOut(
                outcome=LibrarySubmissionOutcome.failed,
                item=None,
                duplicate_of=None,
                message=messages.LIBRARY_STORAGE_FAILED,
            )
        manuscript.file_ref = str(dest)
        await session.commit()
    finally:
        # Every early return above, and any crash, still clears the pending
        # upload; after `os.replace` this is a no-op.
        pending.unlink(missing_ok=True)

    try:
        result = await ingest_manuscript(
            session,
            manuscript,
            dest,
            settings,
            read_images=False,
            _preparation_lease=preparation_lease,
        )
    except VeridicalError:
        return await _fail(
            session,
            entry,
            manuscript,
            manuscript.ingest_failure_detail or messages.LIBRARY_FILE_UNREADABLE,
            settings,
            preparation_lease=preparation_lease,
        )
    except Exception:
        logger.exception("Library submission %s failed during extraction", manuscript.id)
        return await _fail(
            session,
            entry,
            manuscript,
            messages.LIBRARY_FILE_UNREADABLE,
            settings,
            preparation_lease=preparation_lease,
        )

    _fill_from_title_page(entry, result, settings)
    # Committed now: only the detail columns change (never `status`), so this
    # cannot undo a concurrent removal, and the guarded transitions below can
    # re-read the entry without losing the title-page proposals.
    await session.commit()
    document, passage_inventory = await _embed(
        bind_analytical_source(result, manuscript.content_hash),
        settings,
        preparation_lease=preparation_lease,
    )
    if document.whole_document is None:
        return await _fail(
            session,
            entry,
            manuscript,
            messages.LIBRARY_NO_READABLE_TEXT,
            settings,
            preparation_lease=preparation_lease,
        )

    duplicate = await _own_possible_duplicate(
        session, instructor_id, manuscript.id, document.whole_document, settings
    )
    if duplicate is not None:
        duplicate_entry, duplicate_manuscript, similarity = duplicate
        async with advisory_lock(session, manuscript.id):
            if not await _still(session, entry, manuscript, LibraryEntryStatus.processing):
                await _discard_late_work(
                    session, entry, manuscript, settings, preparation_lease=preparation_lease
                )
                return _removed_while_reading()
            entry.status = LibraryEntryStatus.needs_decision
            entry.possible_duplicate_of_id = duplicate_entry.id
            await write_audit_event(
                session,
                event_type="library_entry_needs_decision",
                check_run_id=None,
                manuscript_id=manuscript.id,
                payload={
                    "possible_duplicate_of_manuscript_id": duplicate_manuscript.id,
                    "similarity": round(similarity, 4),
                    "threshold": settings.reuse_exact_duplicate_threshold,
                },
            )
            await session.commit()
        return LibrarySubmissionOut(
            outcome=LibrarySubmissionOutcome.possible_duplicate,
            item=await get_library_item(session, instructor_id, manuscript.id),
            duplicate_of=_duplicate_ref(duplicate_entry, duplicate_manuscript),
            message=messages.LIBRARY_POSSIBLE_DUPLICATE,
        )

    if not await _activate(
        session,
        entry,
        manuscript,
        document,
        passage_inventory.passages,
        settings,
        expected=LibraryEntryStatus.processing,
        decision=None,
        preparation_lease=preparation_lease,
        passage_inventory=passage_inventory,
    ):
        return _removed_while_reading()
    return LibrarySubmissionOut(
        outcome=LibrarySubmissionOutcome.added,
        item=await get_library_item(session, instructor_id, manuscript.id),
        duplicate_of=None,
        message=messages.LIBRARY_ADDED,
    )


async def _remove(
    session: AsyncSession,
    entry: LibraryEntry,
    manuscript: Manuscript,
    settings: Settings,
    *,
    reason: LibraryRemovalReason,
) -> LibraryEntryStateOut:
    """Leaving the Library deletes the comparison data. A Library-only
    submission has no other purpose, so its stored file goes too. A legacy
    entry is a checked manuscript whose report still needs its file, so
    only the comparison data leaves."""
    async with advisory_lock(session, manuscript.id):
        await session.refresh(entry)
        await session.refresh(manuscript, with_for_update=True)
        if entry.status == LibraryEntryStatus.removed:
            raise NotFoundError(f"No Library record with id {manuscript.id}.")
        # A live upload is still being read on another request; removing it
        # now would race that request. A stalled one is safe to remove.
        if entry.status == LibraryEntryStatus.processing and not _is_stalled(entry):
            raise ConflictError(messages.LIBRARY_STILL_READING)
        await delete_archive_rows(session, manuscript.id)
        now = datetime.now(UTC)
        file_deleted = False
        if entry.source == LibraryEntrySource.library_submission:
            # Unconditional and idempotent: a file re-created after an
            # earlier purge must not outlive this removal.
            await asyncio.get_running_loop().run_in_executor(
                None, delete_stored_files, manuscript, settings
            )
            if manuscript.purged_at is None:
                manuscript.purged_at = now
            file_deleted = True
        entry.status = LibraryEntryStatus.removed
        entry.removed_at = now
        entry.removal_reason = reason
        await write_audit_event(
            session,
            event_type="library_entry_removed",
            check_run_id=None,
            manuscript_id=manuscript.id,
            payload={
                "source": entry.source.value,
                "reason": reason.value,
                "file_deleted": file_deleted,
            },
        )
        await session.commit()
    return LibraryEntryStateOut(
        manuscript_id=manuscript.id,
        status=entry.status,
        added_at=entry.added_at,
        removed_at=entry.removed_at,
        file_deleted=file_deleted,
    )


async def remove_library_entry(
    session: AsyncSession, instructor_id: int, manuscript_id: int, settings: Settings
) -> LibraryEntryStateOut:
    entry, manuscript = await _owned_entry(session, instructor_id, manuscript_id)
    return await _remove(
        session, entry, manuscript, settings, reason=LibraryRemovalReason.instructor
    )


async def decide_library_entry(
    session: AsyncSession,
    instructor_id: int,
    manuscript_id: int,
    action: str,
    settings: Settings,
) -> LibraryEntryStateOut:
    """Ground rule 1 for duplicates: only the instructor resolves a
    `needs_decision` entry. "add" re-embeds from the stored extraction (the
    vectors were not kept while the entry waited); "skip" removes it like
    any other Library-only submission."""
    entry, manuscript = await _owned_entry(session, instructor_id, manuscript_id)
    if entry.status != LibraryEntryStatus.needs_decision:
        raise ConflictError(messages.LIBRARY_NOT_AWAITING_DECISION)
    if action == "skip":
        return await _remove(
            session, entry, manuscript, settings, reason=LibraryRemovalReason.duplicate_skipped
        )
    try:
        result = await read_source(
            session, manuscript, lambda: load_raw_store_async(settings, manuscript.id)
        )
    except FileNotFoundError as exc:
        raise ConflictError(messages.LIBRARY_SOURCE_MISSING) from exc
    # Activation acquires advisory before UPDATE and revalidates expected state.
    # Release this read-only SHARE transaction before that mutation boundary.
    await session.commit()
    document, passage_inventory = await _embed(
        bind_analytical_source(result, manuscript.content_hash), settings
    )
    if document.whole_document is None:
        raise ConflictError(messages.LIBRARY_SOURCE_MISSING)
    if not await _activate(
        session,
        entry,
        manuscript,
        document,
        passage_inventory.passages,
        settings,
        expected=LibraryEntryStatus.needs_decision,
        decision="add_anyway",
        passage_inventory=passage_inventory,
    ):
        raise ConflictError(messages.LIBRARY_NOT_AWAITING_DECISION)
    return LibraryEntryStateOut(
        manuscript_id=manuscript.id,
        status=entry.status,
        added_at=entry.added_at,
        removed_at=None,
        passage_indexing=project_passage_indexing(
            entry.passage_indexing_snapshot, manuscript.content_hash, document.model_id
        ),
    )


async def update_library_entry(
    session: AsyncSession,
    instructor_id: int,
    manuscript_id: int,
    update: LibraryEntryUpdate,
    settings: Settings,
) -> LibraryItemOut:
    entry, _ = await _owned_entry(session, instructor_id, manuscript_id)
    changed = sorted(update.model_fields_set)
    for field in changed:
        value = getattr(update, field)
        if field == "authors":
            entry.authors = _clean_authors(value, settings)
        elif field == "program":
            entry.program_id = await _program_id(session, value)
        else:
            setattr(entry, field, _clean_text(value))
    # An edited field is no longer a title-page proposal.
    entry.proposed_fields = [f for f in (entry.proposed_fields or []) if f not in changed]
    if changed:
        await write_audit_event(
            session,
            event_type="library_entry_edited",
            check_run_id=None,
            manuscript_id=manuscript_id,
            payload={"fields": changed},
        )
    await session.commit()
    return await get_library_item(session, instructor_id, manuscript_id)


async def _resolve_readable_library_manuscript(
    session: AsyncSession, instructor_id: int, manuscript_id: int
) -> Manuscript:
    """V-079: active Library files are readable by authenticated instructors.

    The same visibility predicate as the collection keeps ordinary check uploads
    and removed records private; a pending/failed entry is readable only by its
    owner. This authorization never grants edit, decision or removal rights.
    """
    manuscript = await session.scalar(
        select(Manuscript)
        .join(LibraryEntry, LibraryEntry.manuscript_id == Manuscript.id)
        .where(Manuscript.id == manuscript_id, _visible_to(instructor_id))
    )
    if manuscript is None:
        raise NotFoundError(f"No Library record with id {manuscript_id}.")
    await lock_source(session, manuscript)
    # A removal can finish while the SHARE lock waits. Recheck current
    # membership in a fresh statement, including legacy entries whose bytes stay.
    visible = await session.scalar(
        select(LibraryEntry.id)
        .join(Manuscript, Manuscript.id == LibraryEntry.manuscript_id)
        .where(LibraryEntry.manuscript_id == manuscript_id, _visible_to(instructor_id))
    )
    if visible is None:
        raise NotFoundError(f"No Library record with id {manuscript_id}.")
    return manuscript


async def get_library_document(
    session: AsyncSession, instructor_id: int, manuscript_id: int, settings: Settings
) -> ManuscriptViewerOut:
    manuscript = await _resolve_readable_library_manuscript(session, instructor_id, manuscript_id)
    viewer = await read_source(
        session,
        manuscript,
        lambda: manuscript_viewer_for(manuscript, [], settings),
        allow_purged=True,
    )
    # Shared document prose may identify its authors; structured filenames stay
    # redacted just like the collection's metadata (V-079 sharing boundary).
    if manuscript.instructor_id != instructor_id:
        return viewer.model_copy(update={"original_filename": None})
    return viewer


async def get_library_file_path(
    session: AsyncSession, instructor_id: int, manuscript_id: int, settings: Settings
) -> Path:
    manuscript = await _resolve_readable_library_manuscript(session, instructor_id, manuscript_id)
    return await read_source(
        session,
        manuscript,
        lambda: asyncio.to_thread(manuscript_file_path_for, manuscript, settings),
    )


async def get_library_paragraphs(
    session: AsyncSession, instructor_id: int, manuscript_id: int, settings: Settings
) -> DocumentParagraphsOut:
    manuscript = await _resolve_readable_library_manuscript(session, instructor_id, manuscript_id)
    return await read_source(
        session,
        manuscript,
        lambda: asyncio.to_thread(manuscript_paragraphs_for, manuscript, settings),
    )


async def get_library_original_file(
    session: AsyncSession, instructor_id: int, manuscript_id: int, settings: Settings
) -> tuple[Path, str]:
    """V-079: the original PDF/DOCX, including DOCX layout unavailable in the
    extracted-text reader. Same active-Library authorization as the full reader."""
    manuscript = await _resolve_readable_library_manuscript(session, instructor_id, manuscript_id)
    if manuscript.purged_at is not None:
        raise GoneError("This manuscript's source file was purged and can no longer be read.")

    def resolve_original() -> tuple[Path, str]:
        # Client construction can perform blocking credential discovery as well
        # as retrieval; keep both operations off the request's event loop.
        return readable_source_file(manuscript, settings)

    try:
        return await read_source(session, manuscript, lambda: asyncio.to_thread(resolve_original))
    except FileNotFoundError:
        raise GoneError(messages.MANUSCRIPT_SOURCE_MISSING) from None


async def get_library_docx_image(
    session: AsyncSession,
    instructor_id: int,
    manuscript_id: int,
    image_index: int,
    settings: Settings,
) -> tuple[bytes, str]:
    manuscript = await _resolve_readable_library_manuscript(session, instructor_id, manuscript_id)
    return await read_source(
        session,
        manuscript,
        lambda: asyncio.to_thread(manuscript_docx_image_for, manuscript, image_index, settings),
    )


async def get_library_excerpt(
    session: AsyncSession, instructor_id: int, manuscript_id: int, settings: Settings
) -> LibraryExcerptOut:
    """The bounded, cross-tenant-safe view of a Library entry: one
    representative passage per chapter, up to `library_excerpt_max_chapters`,
    re-served from data persisted at embed time. Never opens a stored file.

    V-077: authorized by Library visibility, not merely by the id existing.
    A checked manuscript that is not in the Library, or a removed entry,
    returns the same 404 as an unknown id, so guessing ids confirms nothing."""
    row = (
        await session.execute(
            select(Manuscript)
            .join(LibraryEntry, LibraryEntry.manuscript_id == Manuscript.id)
            .where(Manuscript.id == manuscript_id, _visible_to(instructor_id))
        )
    ).first()
    if row is None:
        raise NotFoundError(f"No Library record with id {manuscript_id}.")
    manuscript = row[0]
    if manuscript.purged_at is not None:
        return LibraryExcerptOut(
            manuscript_id=manuscript_id,
            chapters=[],
            total_chapters=0,
            limitations=(
                "This manuscript's stored content was deleted and can no longer be previewed."
            ),
            purged_at=manuscript.purged_at,
        )

    chapter_rows = (
        await session.scalars(
            select(ManuscriptChapterArchive)
            .where(ManuscriptChapterArchive.manuscript_id == manuscript_id)
            .order_by(ManuscriptChapterArchive.chapter_index)
        )
    ).all()
    total_chapters = len(chapter_rows)
    shown = chapter_rows[: settings.library_excerpt_max_chapters]

    # One representative passage per shown chapter: the FIRST body passage
    # (not reference list, not block quote) in chapter order, the same
    # default-exclusion policy F7.4 matching uses. A chapter with no
    # qualifying passage shows no excerpt rather than a fabricated one.
    excerpts: dict[int, tuple[str | None, str | None, str | None]] = {}
    if shown:
        chapter_indices = [c.chapter_index for c in shown]
        passage_rows = (
            await session.scalars(
                select(ManuscriptPassageArchive)
                .where(
                    ManuscriptPassageArchive.manuscript_id == manuscript_id,
                    ManuscriptPassageArchive.chapter_index.in_(chapter_indices),
                    ManuscriptPassageArchive.is_reference_list.is_(False),
                    ManuscriptPassageArchive.is_block_quote.is_(False),
                )
                .order_by(
                    ManuscriptPassageArchive.chapter_index, ManuscriptPassageArchive.passage_index
                )
            )
        ).all()
        for passage in passage_rows:
            if passage.chapter_index in excerpts:
                continue
            before, after = split_context(passage.context_text, passage.text)
            excerpts[passage.chapter_index] = (before, passage.text, after)

    def _chapter_out(c: ManuscriptChapterArchive) -> LibraryChapterExcerptOut:
        before, excerpt, after = excerpts.get(c.chapter_index, (None, None, None))
        return LibraryChapterExcerptOut(
            chapter_index=c.chapter_index,
            title=c.title,
            excerpt=excerpt,
            context_before=before,
            context_after=after,
        )

    return LibraryExcerptOut(
        manuscript_id=manuscript_id,
        chapters=[_chapter_out(c) for c in shown],
        total_chapters=total_chapters,
        limitations=(
            "This preview shows one representative passage per chapter, up to "
            f"{settings.library_excerpt_max_chapters} chapters. Signed-in instructors can "
            "open the full manuscript for active Library records."
        ),
    )
