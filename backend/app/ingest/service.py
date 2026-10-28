"""Ingestion orchestration: file → extraction → DB row + raw store.

The ingest stage of the pipeline (ENGINEERING.md §4). Extraction itself is
CPU-bound and runs in the owned preparation executor; this module owns the status
transitions on the manuscript row and the raw-store write.
"""

import asyncio
import hashlib
import logging
import zipfile
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import messages
from app.audit.service import write_audit_event
from app.checks.escalation import NEEDS_REVIEW_OUTCOMES
from app.config import Settings, get_settings
from app.errors import (
    ApiDownError,
    ConflictError,
    FileMalformedError,
    FileTooLargeError,
    InvalidApiKeyError,
    NotFoundError,
    QuotaExhaustedError,
)
from app.groups.assignment import (
    AssignmentReceipt,
    AssignmentRequest,
    assignment_values,
    confirmation_recorded,
    current_receipt,
)
from app.groups.service import (
    UNSET_PROGRAM_FILTER,
    match_or_create_group_from_proposal,
    program_name_for,
    resolve_or_create_group,
    validate_group_fields,
)
from app.ingest import docx, figures, pdf, references, vision
from app.ingest.admission import PreparationLease, preparation_scope
from app.ingest.patterns import load_patterns
from app.ingest.recovery import recover_layout, selected_pages
from app.ingest.schemas import (
    CurrentGroupOut,
    ExtractionResult,
    ManuscriptGroupOut,
    ManuscriptListItem,
    ManuscriptQueueStatus,
    ManuscriptSort,
    PaginatedManuscripts,
)
from app.ingest.titlepage import TitlePageProposal, extract_title_page
from app.ingest.zip_bounds import check_docx_directory_size
from app.llm import LLMNotConfiguredError, get_llm_client_for
from app.models.citation import Citation
from app.models.enums import (
    CheckRunStatus,
    IngestFailureReason,
    IngestStatus,
    ManuscriptOrigin,
)
from app.models.group import Group, GroupMember, Program
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckResult, CheckRun, ReadinessReport
from app.storage import ensure_local_file, get_storage, storage_key_for
from app.storage.access import read_source

# One extractor per supported suffix. Each is a sync callable executed off
# the event loop. Legacy .doc is deliberately absent: rejected with a clear
# user-fixable message (V-005 edge case).
EXTRACTORS: dict[str, Callable[[str, Settings], ExtractionResult]] = {
    ".pdf": pdf.extract_document,
    ".docx": docx.extract_document,
}

logger = logging.getLogger(__name__)


def raw_store_path(settings: Settings, manuscript_id: int) -> Path:
    return settings.data_dir / f"{manuscript_id}.extraction.json"


def select_extractor(suffix: str) -> Callable[[str, Settings], ExtractionResult]:
    extractor = EXTRACTORS.get(suffix.lower())
    if extractor is None:
        raise FileMalformedError(
            messages.UNSUPPORTED_FILE_TYPE.format(
                suffix=suffix or "(none)", supported=", ".join(sorted(EXTRACTORS))
            )
        )
    return extractor


# Magic numbers are format invariants: %PDF, ZIP local-file header (a DOCX
# is a zip), OLE compound file (legacy .doc).
_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def detect_format(file_path: Path, settings: Settings | None = None) -> str:
    """Sniff the REAL type from content — extensions are user input and
    lie (a DOCX renamed to .pdf must still parse as a DOCX, V-008 edge
    case). Returns an EXTRACTORS key or the honest non-key (".doc") so
    select_extractor can name what was actually uploaded."""
    with file_path.open("rb") as fh:
        head = fh.read(len(_OLE_MAGIC))
    if head.startswith(_PDF_MAGIC):
        return ".pdf"
    if head.startswith(_ZIP_MAGIC):
        check_docx_directory_size(file_path, settings or get_settings())
        try:
            with zipfile.ZipFile(file_path) as zf:
                if any(name.startswith("word/") for name in zf.namelist()):
                    return ".docx"
        except zipfile.BadZipFile:
            raise FileMalformedError(messages.FILE_UNREADABLE) from None
    if head.startswith(_OLE_MAGIC):
        return ".doc"  # legacy Word: recognized, then rejected by name
    raise FileMalformedError(
        messages.FILE_UNRECOGNIZED.format(supported=", ".join(sorted(EXTRACTORS)))
    )


async def save_upload(chunks: AsyncIterator[bytes], dest: Path, settings: Settings) -> Path:
    """Stream an upload to disk, enforcing the size ceiling WHILE reading —
    an oversized file is rejected at the limit, not after it landed."""
    limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dest.open("wb") as out:
            async for chunk in chunks:
                written += len(chunk)
                if written > limit:
                    raise FileTooLargeError(
                        messages.FILE_TOO_LARGE.format(limit_mb=settings.max_upload_mb)
                    )
                out.write(chunk)
    except FileTooLargeError:
        dest.unlink(missing_ok=True)
        raise
    return dest


def hash_file_sha256(path: Path, chunk_bytes: int) -> str:
    """SHA-256 hex digest of the file's raw bytes (BUG-140 content
    identity) — chunked read, never loads the whole (up to
    `max_upload_mb`) file into memory at once."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_original_filename(filename: str) -> str | None:
    """Display-only filename (BUG-022): path components stripped OS-
    independently, control characters (including NUL) dropped, cut to the
    column's 255 limit. Never used on the filesystem."""
    clean_name = "".join(ch for ch in filename.replace("\\", "/") if ch.isprintable())
    return Path(clean_name).name[:255] or None


def _recover_with_cache(
    result: ExtractionResult, file_path: Path, settings: Settings, manuscript_id: int
) -> ExtractionResult:
    previous = None
    if settings.ingest_recovery_python is not None and selected_pages(result):
        # Optional reuse only. A missing, unreadable or unavailable cache
        # never discards fresh native work. The later durable write must
        # still succeed; storage failures are not suppressed there.
        with suppress(OSError, ValueError, BotoCoreError, ClientError):
            previous = load_raw_store(settings, manuscript_id).recovery
    return recover_layout(result, file_path, settings, previous)


async def ingest_manuscript(
    session: AsyncSession,
    manuscript: Manuscript,
    file_path: Path,
    settings: Settings | None = None,
    *,
    read_images: bool = True,
    _preparation_lease: PreparationLease | None = None,
) -> ExtractionResult:
    """Admit direct callers too; nested uploads borrow the same preparation owner."""
    settings = settings or get_settings()
    with preparation_scope(settings, _preparation_lease) as lease:
        return await _ingest_manuscript(
            session,
            manuscript,
            file_path,
            settings,
            read_images=read_images,
            preparation_lease=lease,
        )


async def _ingest_manuscript(
    session: AsyncSession,
    manuscript: Manuscript,
    file_path: Path,
    settings: Settings | None = None,
    *,
    read_images: bool = True,
    preparation_lease: PreparationLease,
) -> ExtractionResult:
    """`read_images=False` (V-077, Library submissions) skips the vision
    pass, the only Gemini call in ingestion: a Library manuscript is stored
    for text comparison only, so it costs zero quota. `vision_status` then
    stays "none", an honest "not read", never "done"."""
    settings = settings or get_settings()
    manuscript_id, instructor_id = manuscript.id, manuscript.instructor_id
    manuscript.ingest_status = IngestStatus.processing
    await session.commit()
    finalizing = False
    try:
        # Dispatch by sniffed content (extensions lie) — inside the stage
        # boundary so an unreadable file leaves the row `failed`, not stuck.
        source_format = detect_format(file_path)
        extractor = select_extractor(source_format)
        # BUG-180: `ingest_max_pages`/`max_docx_uncompressed_mb` are the
        # fix that actually bounds the work (checked before it starts);
        # this timeout is a second, independent backstop for the case
        # where per-file cost is unexpectedly high even under those caps.
        # Disclosed limitation: `wait_for` bounds how long THIS REQUEST
        # waits, not how long the underlying synchronous CPU work in the
        # threadpool actually runs -- Python cannot forcibly cancel a
        # running thread, so a timed-out extraction's thread keeps
        # running to completion in the background, its result discarded.
        try:
            result = await asyncio.wait_for(
                preparation_lease.run(extractor, str(file_path), settings),
                timeout=settings.ingest_extraction_timeout_seconds,
            )
        except TimeoutError as exc:
            raise FileTooLargeError(messages.EXTRACTION_TIMED_OUT) from exc
        # Only the fresh sniffed DOCX parser supplies this witness. Stage it
        # locally until finalization; optional consumers may commit their own
        # bookkeeping before ingestion is ready to publish its terminal row.
        fresh_docx_hash = result.docx_source_sha256 if source_format == ".docx" else None
        # Separate optional CPU process, with its own bounds and native-source
        # mappings. It cannot erase an unresolved reading-order record.
        result = await preparation_lease.run(
            _recover_with_cache, result, file_path, settings, manuscript.id
        )
        patterns = load_patterns(settings.ingest_patterns_file)
        drafts = await preparation_lease.run(references.extract_references, result, patterns)
        selected_images = vision.prepare_image_readings(
            result, patterns, settings, enabled=read_images
        )
        try:
            # Vision pass (F1.3) merges image tables/equations into result
            # BEFORE the raw store is written, so the store is complete.
            if selected_images:
                llm = await get_llm_client_for(session, settings, manuscript.instructor_id)
                await vision.read_images(
                    result, file_path, llm, patterns, settings, preparation_lease=preparation_lease
                )
        except (LLMNotConfiguredError, ApiDownError, QuotaExhaustedError) as exc:
            # No client (real mode before V-009), or the queue's own
            # retries were exhausted, or the daily quota ran out: image
            # content stays unread — an honest state (F1.7), never an
            # ingestion failure. Found live (V2 milestone demo,
            # 2026-07-25): a real Gemini vision-call outage previously
            # propagated through the outer `except Exception` below and
            # failed the WHOLE manuscript — the text/structure/references
            # this stage already extracted are still perfectly good and
            # must not be thrown away over an unrelated image-reading
            # problem.
            result.vision_status = "unavailable"
            vision.mark_pending_unavailable(
                result,
                "client_unavailable"
                if isinstance(exc, LLMNotConfiguredError)
                else "quota_exhausted"
                if isinstance(exc, QuotaExhaustedError)
                else "api_down",
            )
        # Visual observations remain separate from numeric image transcriptions
        # and never enter semantic grading or integrity scoring.
        selected_figures = figures.prepare_figures(result, patterns, settings, enabled=read_images)
        if selected_figures:
            try:
                llm = await get_llm_client_for(session, settings, manuscript.instructor_id)
                await figures.read_figures(
                    result,
                    file_path,
                    llm,
                    patterns,
                    settings,
                    source_hash=manuscript.content_hash or fresh_docx_hash,
                    preparation_lease=preparation_lease,
                )
            except (LLMNotConfiguredError, ApiDownError, QuotaExhaustedError, InvalidApiKeyError):
                for entry in result.figure_readings or []:
                    if entry.reason == "not_attempted":
                        entry.status, entry.reason = "unavailable", "service_unavailable"
        raw_path = raw_store_path(settings, manuscript.id)
        await preparation_lease.run(_write_raw_store, result, raw_path)
        # BUG-138: same durability as the upload itself -- without this, the
        # extraction JSON survives only until the next redeploy/idle-wake,
        # and every resumed check run past that point dies deep in the
        # pipeline with a raw FileNotFoundError (the exact failure this
        # ticket measured on check run 5, `GROUND.pdf`).
        storage = get_storage(settings)
        await preparation_lease.run(
            storage.put_file, raw_path, storage_key_for(settings, str(raw_path))
        )
        # Citation replacement and the terminal state are one transaction.
        finalizing = True
        # Explicit fresh ingestion may enter below the upload boundary. Never
        # replace an existing identity, including a mismatch, or infer one from
        # an older raw-store read. Roll back this assignment with finalization.
        if manuscript.content_hash is None and fresh_docx_hash:
            manuscript.content_hash = fresh_docx_hash
        await session.execute(delete(Citation).where(Citation.manuscript_id == manuscript_id))
        session.add_all(Citation(manuscript_id=manuscript_id, **asdict(draft)) for draft in drafts)
        manuscript.section_tree = result.section_tree.model_dump()
        manuscript.ingest_status = IngestStatus.done
        await session.commit()
    except FileMalformedError as exc:
        # Stage boundary (CODING.md §2): the failure is recorded on the row,
        # then propagates — run-level stage bookkeeping arrives with V-018.
        # BUG-016: the row must say why, not just that it failed.
        # BUG-066: `str(exc)` is safe to persist here ONLY because every
        # `FileMalformedError` raise site passes a vetted `messages.py`
        # template (FILE_UNREADABLE/FILE_ENCRYPTED), never raw file content.
        await _record_ingest_failure(
            session, manuscript_id, instructor_id, IngestFailureReason.unreadable_format, str(exc)
        )
        raise
    except FileTooLargeError as exc:
        # BUG-180 (found while adding this ticket's own page-count/timeout
        # guards): `FileTooLargeError` -- already raised here by the
        # pre-existing DOCX zip-bomb guard (BUG-159, `docx.py`), and now
        # also by the PDF page-count cap and the extraction timeout below
        # -- IS-A `Exception`, so without this explicit branch every one of
        # those cases fell through to the generic handler below and
        # recorded the wrong, less specific `extraction_failed` reason
        # instead of the honest `file_too_large` `save_upload`'s own
        # size-cap already uses for the exact same failure class.
        # BUG-066: `str(exc)` carries the SPECIFIC vetted message each raise
        # site already computed (real MB/page numbers from config) -- three
        # different underlying caps (raw upload MB, PDF page count, DOCX
        # decompressed MB) share this one enum bucket, so a single generic
        # "over the MB limit" sentence would be flatly wrong for two of the
        # three. Never a raw caught exception's `str()` -- only ever this
        # library's own vetted `VeridicalError` message.
        await _record_ingest_failure(
            session, manuscript_id, instructor_id, IngestFailureReason.file_too_large, str(exc)
        )
        raise
    except Exception:
        await _record_ingest_failure(
            session,
            manuscript_id,
            instructor_id,
            IngestFailureReason.extraction_failed,
            messages.MANUSCRIPT_PREPARATION_FAILED if finalizing else None,
        )
        raise
    return result


async def _record_ingest_failure(
    session: AsyncSession,
    manuscript_id: int,
    instructor_id: int,
    reason: IngestFailureReason,
    detail: str | None,
) -> None:
    """Best effort after a failed stage; the caller re-raises its original error."""
    try:
        # Rollback expires ORM attributes, so use only captured identity here.
        await session.rollback()
        changed = await session.execute(
            update(Manuscript)
            .where(
                Manuscript.id == manuscript_id,
                Manuscript.instructor_id == instructor_id,
                Manuscript.purged_at.is_(None),
            )
            .values(
                ingest_status=IngestStatus.failed,
                ingest_failure_reason=reason,
                ingest_failure_detail=detail,
            )
            .returning(Manuscript.id)
            .execution_options(synchronize_session=False)
        )
        updated_id = changed.scalar_one_or_none()
        await session.commit()
        if updated_id is not None:
            # Rollback can be a no-op before a transaction starts. Reload even
            # then so callers cannot retain the cached processing state.
            await session.get(Manuscript, manuscript_id, populate_existing=True)
    except Exception as recovery_error:
        # A persistent outage can prevent recording failure. Never mask the
        # original error or copy database/document exception text into logs.
        logger.warning(
            "Could not record manuscript preparation failure (%s)", type(recovery_error).__name__
        )
        with suppress(Exception):
            await session.rollback()


async def ingest_upload(
    session: AsyncSession,
    chunks: AsyncIterator[bytes],
    filename: str,
    group_label: str,
    settings: Settings | None = None,
    *,
    instructor_id: int,
) -> tuple[Manuscript, ExtractionResult, int, TitlePageProposal, Manuscript | None]:
    """Reject occupied preparation before creating groups, rows or stored uploads."""
    settings = settings or get_settings()
    with preparation_scope(settings) as lease:
        return await _ingest_upload(
            session,
            chunks,
            filename,
            group_label,
            settings,
            instructor_id=instructor_id,
            preparation_lease=lease,
        )


async def _ingest_upload(
    session: AsyncSession,
    chunks: AsyncIterator[bytes],
    filename: str,
    group_label: str,
    settings: Settings | None = None,
    *,
    instructor_id: int,
    preparation_lease: PreparationLease,
) -> tuple[Manuscript, ExtractionResult, int, TitlePageProposal, Manuscript | None]:
    """Full upload flow for the HTTP surface: save (size-capped) → own row
    → ingest. `instructor_id` is the authenticated caller (BUG-002/D-020) —
    this endpoint used to attach uploads to whichever instructor had the
    lowest id, reachable with no login at all; fixed same session it was
    found."""
    settings = settings or get_settings()
    # BUG-022: group_label defaults to a constant ("Ungrouped"), so it
    # alone can't tell two manuscripts apart in a picker/list — the
    # instructor's own filename usually can. `.replace("\\", "/")` before
    # `.name` makes path-component stripping OS-independent — bare
    # `Path(...).name` only splits on the HOST's own separator, and
    # production runs on Linux (D-003), so a raw Windows-style path from
    # a non-browser client would otherwise pass through untouched.
    # `isprintable()` drops control characters, including a literal NUL
    # byte — verified live: an unescaped NUL in `filename` 500'd this
    # endpoint before this filter existed (Postgres's UTF8 encoding
    # rejects NUL outright; backend review finding, BUG-022 review).
    # `[:255]` matches the column's own limit so an unusually long
    # filename never 500s here either.
    original_filename = clean_original_filename(filename)
    # V-062: group_label is no longer stored verbatim -- it's resolved
    # against the instructor's existing groups first (case/whitespace
    # insensitive, AC1), so "Group 4" and "group 4" land on the same row
    # and the manuscript's own group_label becomes whichever spelling that
    # row already settled on.
    group = await resolve_or_create_group(session, instructor_id, group_label)
    manuscript = Manuscript(
        instructor_id=instructor_id,
        group_id=group.id,
        group_label=group.name,
        file_ref="",
        original_filename=original_filename,
    )
    session.add(manuscript)
    await session.commit()

    # Stored under a server-owned name: the id is the identity; the
    # original filename never touches the filesystem (it's persisted for
    # display only, above).
    suffix = Path(filename).suffix.lower()[:8]
    dest = settings.data_dir / settings.manuscript_upload_subdir / f"{manuscript.id}{suffix}"
    try:
        await save_upload(chunks, dest, settings)
    except FileTooLargeError as exc:
        # BUG-066: same vetted-message capture as `ingest_manuscript`'s own
        # `FileTooLargeError` branch above -- this is the raw-upload-size
        # cap specifically (`save_upload`'s own `messages.FILE_TOO_LARGE`),
        # the one case where `max_upload_mb` really is the right number.
        manuscript.ingest_status = IngestStatus.failed
        manuscript.ingest_failure_reason = IngestFailureReason.file_too_large
        manuscript.ingest_failure_detail = str(exc)
        await session.commit()
        raise
    # BUG-140: content identity, computed once here (off the event loop --
    # a chunked SHA-256 read of up to `max_upload_mb`) rather than derived
    # later from bytes that may no longer be on local disk (BUG-138).
    manuscript.content_hash = await preparation_lease.run(
        hash_file_sha256, dest, settings.content_hash_read_chunk_bytes
    )
    # BUG-138: durably persist the upload BEFORE the row claims it exists --
    # a failed R2 write raises here rather than leaving `file_ref` pointing
    # at a copy that only ever lived on Render's ephemeral disk.
    storage = get_storage(settings)
    await preparation_lease.run(storage.put_file, dest, storage_key_for(settings, str(dest)))
    manuscript.file_ref = str(dest)
    await session.commit()
    # BUG-234: the upload is now durably backed and its hash is committed.
    # Query only this authenticated instructor's rows; exposing whether the
    # same bytes exist in another account would create a cross-tenant oracle.
    # Keeping the lookup after the commit also means that, for two concurrent
    # uploads, whichever commit finishes second can see the first. The rows
    # remain distinct check inputs. V-077: only earlier dashboard uploads are
    # "earlier uploads"; Library submissions are a separate collection.
    existing_upload = await session.scalar(
        select(Manuscript)
        .where(
            Manuscript.instructor_id == instructor_id,
            Manuscript.content_hash == manuscript.content_hash,
            Manuscript.id != manuscript.id,
            Manuscript.origin == ManuscriptOrigin.check_upload,
        )
        .order_by(
            Manuscript.purged_at.is_(None).desc(),
            Manuscript.created_at.desc(),
            Manuscript.id.desc(),
        )
        .limit(1)
    )
    result = await ingest_manuscript(
        session, manuscript, dest, settings, _preparation_lease=preparation_lease
    )
    n_citations = (
        await session.scalar(
            select(func.count())
            .select_from(Citation)
            .where(Citation.manuscript_id == manuscript.id)
        )
        or 0
    )
    # V-063: deterministic, no LLM call (Q1 DECIDED) -- proposes only,
    # never applies (the instructor confirms via a separate endpoint).
    patterns = load_patterns(settings.ingest_patterns_file)
    title_page_proposal = extract_title_page(result, patterns)
    return manuscript, result, n_citations, title_page_proposal, existing_upload


def _write_raw_store(result: ExtractionResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(), encoding="utf-8")


async def list_manuscripts(
    session: AsyncSession,
    instructor_id: int,
    *,
    page: int = 1,
    page_size: int = 50,
    q: str | None = None,
    status: ManuscriptQueueStatus | None = None,
    needs_review: bool | None = None,
    group: str | None = None,
    program: str | None = None,
    sort: ManuscriptSort = ManuscriptSort.newest,
) -> PaginatedManuscripts:
    """Server-paginated listing (V-021 edge case: 100+ manuscripts in
    defense season) for both the dashboard table (4e) and the New Check
    modal's manuscript picker (V-018, which just requests a generously
    large page). Each row carries its own latest check_run id/status so
    the dashboard can link "view progress"/"open report" with zero extra
    per-row requests.

    V-071 AC2 applies every search/filter/sort before pagination. ``q``
    searches the instructor-visible group and filename; ``group`` is an
    exact, case-insensitive group filter; ``status`` is the closed Review
    Desk queue vocabulary; and ``needs_review`` is based on criterion-level
    escalations in the same latest DONE run used by the row's report link.

    `program` (V-062, AC5) filters to manuscripts whose group is assigned
    that program -- an inner join, so a manuscript with no group or a
    group whose program is still NULL ("Not set") is correctly excluded
    from any specific-program filter rather than guessed into one.
    `UNSET_PROGRAM_FILTER` is the one sentinel value that asks for exactly
    those excluded rows instead -- a real, selectable filter state, not an
    edge case to hide (design review finding while speccing the dashboard
    control this powers)."""
    # Run identity defines insertion order; created_at records transaction start.
    latest_run_status = (
        select(CheckRun.status)
        .where(CheckRun.manuscript_id == Manuscript.id)
        .order_by(CheckRun.id.desc())
        .limit(1)
        .correlate(Manuscript)
        .scalar_subquery()
    )
    latest_done_run_id = (
        select(CheckRun.id)
        .where(
            CheckRun.manuscript_id == Manuscript.id,
            CheckRun.status == CheckRunStatus.done,
        )
        .order_by(CheckRun.id.desc())
        .limit(1)
        .correlate(Manuscript)
        .scalar_subquery()
    )
    escalation_count = (
        select(func.count(CheckResult.id))
        .where(
            CheckResult.check_run_id == latest_done_run_id,
            CheckResult.criterion_id.is_not(None),
            CheckResult.outcome.in_(NEEDS_REVIEW_OUTCOMES),
        )
        .correlate(Manuscript)
        .scalar_subquery()
    )
    has_decision = (
        select(ReadinessReport.id)
        .where(
            ReadinessReport.check_run_id == latest_done_run_id,
            ReadinessReport.decision.is_not(None),
        )
        .correlate(Manuscript)
        .exists()
    )

    filters = [Manuscript.instructor_id == instructor_id]
    filters.append(Manuscript.dismissed_at.is_(None))
    # V-077 (D-028): Library submissions are not check inputs and never
    # appear on the Review Desk.
    filters.append(Manuscript.origin == ManuscriptOrigin.check_upload)
    count_stmt = (
        select(func.count())
        .select_from(Manuscript)
        .outerjoin(Group, Group.id == Manuscript.group_id)
        .outerjoin(Program, Program.id == Group.program_id)
    )
    manuscripts_stmt = (
        select(Manuscript)
        .outerjoin(Group, Group.id == Manuscript.group_id)
        .outerjoin(Program, Program.id == Group.program_id)
    )

    normalized_q = q.strip() if q else None
    if normalized_q:
        pattern = f"%{normalized_q}%"
        filters.append(
            or_(
                Manuscript.group_label.ilike(pattern),
                Manuscript.original_filename.ilike(pattern),
            )
        )

    normalized_group = group.strip() if group else None
    if normalized_group:
        filters.append(func.lower(Manuscript.group_label) == normalized_group.casefold())

    if program == UNSET_PROGRAM_FILTER:
        filters.append(Program.id.is_(None))
    elif program is not None:
        filters.append(Program.name == program)

    if status == ManuscriptQueueStatus.needs_attention:
        # One reachable instructor-work queue owns both unresolved criterion
        # tasks and recovery states. Keep the union on the server so tenant
        # scoping, filters, totals, sorting, and pagination remain truthful.
        filters.append(
            or_(
                and_(latest_run_status == CheckRunStatus.done, ~has_decision, escalation_count > 0),
                latest_run_status.in_((CheckRunStatus.failed, CheckRunStatus.cancelled)),
                and_(
                    latest_run_status.is_(None),
                    Manuscript.ingest_status == IngestStatus.failed,
                ),
            )
        )
    elif status == ManuscriptQueueStatus.ingestion_failed:
        filters.append(Manuscript.ingest_status == IngestStatus.failed)
    elif status == ManuscriptQueueStatus.not_checked:
        filters.extend(
            [
                Manuscript.purged_at.is_(None),
                Manuscript.ingest_status == IngestStatus.done,
                latest_run_status.is_(None),
            ]
        )
    elif status == ManuscriptQueueStatus.checking:
        filters.append(
            latest_run_status.in_(
                (
                    CheckRunStatus.queued,
                    CheckRunStatus.ingesting,
                    CheckRunStatus.structural,
                    CheckRunStatus.semantic,
                    CheckRunStatus.integrity,
                    CheckRunStatus.aggregating,
                )
            )
        )
    elif status == ManuscriptQueueStatus.check_failed:
        filters.append(latest_run_status == CheckRunStatus.failed)
    elif status == ManuscriptQueueStatus.cancelled:
        filters.append(latest_run_status == CheckRunStatus.cancelled)
    elif status == ManuscriptQueueStatus.checked:
        # Queue statuses are mutually useful instructor work states:
        # `checked` means the absolute-latest run finished and its report is
        # waiting for a human decision, while `decided` below means that
        # decision is already recorded. An older DONE report remains
        # readable while a re-run is active/failed/cancelled, but it must not
        # place that manuscript in two contradictory work queues at once.
        filters.extend([latest_run_status == CheckRunStatus.done, ~has_decision])
    elif status == ManuscriptQueueStatus.decided:
        filters.extend([latest_run_status == CheckRunStatus.done, has_decision])

    if needs_review is True:
        # An older report's escalation remains readable, but it is not a
        # current instructor task while a newer run is active or terminal in
        # a different state. This keeps the work queues mutually coherent.
        filters.extend([latest_run_status == CheckRunStatus.done, escalation_count > 0])
    elif needs_review is False:
        filters.append(escalation_count == 0)

    order_by = {
        ManuscriptSort.newest: (Manuscript.created_at.desc(), Manuscript.id.desc()),
        ManuscriptSort.oldest: (Manuscript.created_at.asc(), Manuscript.id.asc()),
        ManuscriptSort.group_asc: (
            func.lower(Manuscript.group_label).asc(),
            Manuscript.created_at.desc(),
            Manuscript.id.desc(),
        ),
        ManuscriptSort.needs_review_desc: (
            escalation_count.desc(),
            Manuscript.created_at.desc(),
            Manuscript.id.desc(),
        ),
    }[sort]

    total = await session.scalar(count_stmt.where(*filters))
    manuscripts = (
        await session.scalars(
            manuscripts_stmt.where(*filters)
            .order_by(*order_by)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    manuscript_ids = [m.id for m in manuscripts]
    latest_by_manuscript: dict[int, CheckRun] = {}
    # Separate from `latest_by_manuscript` (backend review finding,
    # V-055): a manuscript's absolute-latest run can be a failed/in-flight
    # RE-run that supersedes an earlier DONE run with a perfectly valid,
    # still-readable report. Tracking the latest DONE run too means "Open
    # report" never goes dark just because a newer re-run hasn't finished
    # yet — same class of bug BUG-012 was filed for, caught one level
    # down before it shipped.
    latest_done_by_manuscript: dict[int, CheckRun] = {}
    if manuscript_ids:
        runs = (
            await session.scalars(
                select(CheckRun)
                .where(CheckRun.manuscript_id.in_(manuscript_ids))
                .order_by(
                    CheckRun.manuscript_id,
                    CheckRun.id.desc(),
                )
            )
        ).all()
        for run in runs:
            # First one seen per manuscript is the latest (rows arrive
            # ordered newest-first within each manuscript_id group).
            latest_by_manuscript.setdefault(run.manuscript_id, run)
            if run.status == CheckRunStatus.done:
                latest_done_by_manuscript.setdefault(run.manuscript_id, run)

    def _latest_id(manuscript_id: int) -> int | None:
        run = latest_by_manuscript.get(manuscript_id)
        return run.id if run is not None else None

    def _latest_status(manuscript_id: int) -> str | None:
        run = latest_by_manuscript.get(manuscript_id)
        return run.status.value if run is not None else None

    def _latest_done_id(manuscript_id: int) -> int | None:
        run = latest_done_by_manuscript.get(manuscript_id)
        return run.id if run is not None else None

    # V-038 / UX review finding: one query for every latest-done run's
    # decision, keyed by check_run_id then remapped by manuscript_id below
    # -- avoids an N+1 (one query per row) at defense-season density.
    decision_by_run: dict[int, str] = {}
    readiness_by_run: dict[int, str] = {}
    done_run_ids = [run.id for run in latest_done_by_manuscript.values()]
    if done_run_ids:
        rows = (
            await session.execute(
                select(
                    ReadinessReport.check_run_id,
                    ReadinessReport.decision,
                    ReadinessReport.status,
                ).where(ReadinessReport.check_run_id.in_(done_run_ids))
            )
        ).all()
        decision_by_run = {
            run_id: decision.value for run_id, decision, _status in rows if decision is not None
        }
        readiness_by_run = {run_id: status.value for run_id, _decision, status in rows}

    def _latest_decision(manuscript_id: int) -> str | None:
        run = latest_done_by_manuscript.get(manuscript_id)
        return decision_by_run.get(run.id) if run is not None else None

    def _latest_readiness(manuscript_id: int) -> str | None:
        run = latest_done_by_manuscript.get(manuscript_id)
        return readiness_by_run.get(run.id) if run is not None else None

    # V-041 / UX review finding (P1, live-reproduced against real
    # multi-family seeded data): without this, RerunModal had no signal
    # to tell "checked under the same rubric family, genuinely stale" from
    # "checked under a completely unrelated format" -- it defaulted BOTH
    # to selected, capable of silently submitting a manuscript for
    # grading against a rubric it was never invited to run under, burning
    # real quota (D-001) with no indication anywhere in the row. One
    # query, no N+1, same shape as `decision_by_run` above.
    family_by_run: dict[int, str] = {}
    if done_run_ids:
        rubric_ids = {run.rubric_id for run in latest_done_by_manuscript.values()}
        rubric_rows = (
            await session.execute(
                select(Rubric.id, Rubric.rubric_family_id).where(Rubric.id.in_(rubric_ids))
            )
        ).all()
        family_by_rubric = {rubric_id: str(family_id) for rubric_id, family_id in rubric_rows}
        family_by_run = {
            run.id: family_by_rubric[run.rubric_id]
            for run in latest_done_by_manuscript.values()
            if run.rubric_id in family_by_rubric
        }

    def _latest_done_rubric_family_id(manuscript_id: int) -> str | None:
        run = latest_done_by_manuscript.get(manuscript_id)
        return family_by_run.get(run.id) if run is not None else None

    # V-071 (AC1, BUG-058-adjacent): the dashboard's "N escalations awaiting
    # your review" count had no way to point at WHICH manuscript held them
    # -- first-time-user review's baseline walkthrough had to open reports one at a
    # time, reading each one fully, to find the right row. Same one-query,
    # no-N+1 shape as `decision_by_run` above, scoped to the same latest-
    # done runs.
    escalated_by_run: dict[int, int] = {}
    if done_run_ids:
        rows = (
            await session.execute(
                select(CheckResult.check_run_id, func.count())
                .where(
                    CheckResult.check_run_id.in_(done_run_ids),
                    CheckResult.criterion_id.is_not(None),
                    CheckResult.outcome.in_(NEEDS_REVIEW_OUTCOMES),
                )
                .group_by(CheckResult.check_run_id)
            )
        ).all()
        escalated_by_run = dict(rows)

    def _escalations_awaiting_review(manuscript_id: int) -> int:
        run = latest_done_by_manuscript.get(manuscript_id)
        return escalated_by_run.get(run.id, 0) if run is not None else 0

    # V-062 (AC5): each row's program, sourced through its group -- every
    # row needs this for display regardless of whether `program` filtered
    # this query, so it's a separate lookup, not reused from the filter
    # join above (which only ran when a filter was actually given).
    program_by_manuscript: dict[int, str] = {}
    if manuscript_ids:
        rows = (
            await session.execute(
                select(Manuscript.id, Program.name)
                .join(Group, Group.id == Manuscript.group_id)
                .join(Program, Program.id == Group.program_id)
                .where(Manuscript.id.in_(manuscript_ids))
            )
        ).all()
        program_by_manuscript = dict(rows)

    def _program(manuscript_id: int) -> str | None:
        return program_by_manuscript.get(manuscript_id)

    items = [
        ManuscriptListItem(
            id=m.id,
            purged_at=m.purged_at,
            group_label=m.group_label,
            program=_program(m.id),
            original_filename=m.original_filename,
            ingest_status=m.ingest_status.value,
            ingest_failure_reason=(
                m.ingest_failure_reason.value if m.ingest_failure_reason else None
            ),
            ingest_failure_detail=m.ingest_failure_detail,
            created_at=m.created_at,
            latest_check_run_id=_latest_id(m.id),
            latest_check_run_status=_latest_status(m.id),
            latest_done_check_run_id=_latest_done_id(m.id),
            latest_decision=_latest_decision(m.id),
            latest_readiness=_latest_readiness(m.id),
            latest_done_rubric_family_id=_latest_done_rubric_family_id(m.id),
            escalations_awaiting_review=_escalations_awaiting_review(m.id),
        )
        for m in manuscripts
    ]
    return PaginatedManuscripts(items=items, total=total or 0, page=page, page_size=page_size)


async def dismiss_failed_manuscript(
    session: AsyncSession, instructor_id: int, manuscript_id: int
) -> Manuscript:
    """Move a permanently failed upload out of the active Review Desk.

    This is intentionally retention, not deletion: the manuscript row remains
    in the instructor's Archive and an immutable audit event records the
    transition. Repeating the request is idempotent.
    """
    dismissed_at = datetime.now(UTC)
    manuscript = await session.scalar(
        update(Manuscript)
        .where(
            Manuscript.id == manuscript_id,
            Manuscript.instructor_id == instructor_id,
            Manuscript.ingest_status == IngestStatus.failed,
            Manuscript.dismissed_at.is_(None),
            Manuscript.origin == ManuscriptOrigin.check_upload,
        )
        .values(dismissed_at=dismissed_at)
        .returning(Manuscript)
    )
    if manuscript is None:
        current = await session.scalar(
            select(Manuscript).where(
                Manuscript.id == manuscript_id,
                Manuscript.instructor_id == instructor_id,
                Manuscript.origin == ManuscriptOrigin.check_upload,
            )
        )
        if current is None:
            raise NotFoundError(f"No manuscript with id {manuscript_id}.")
        if current.ingest_status != IngestStatus.failed:
            raise ConflictError("Only a permanently failed upload can be dismissed.")
        return current

    await write_audit_event(
        session,
        event_type="manuscript_ingestion_failure_dismissed",
        check_run_id=None,
        manuscript_id=manuscript.id,
        payload={
            "manuscript_id": manuscript.id,
            "ingest_failure_reason": (
                manuscript.ingest_failure_reason.value
                if manuscript.ingest_failure_reason is not None
                else None
            ),
        },
    )
    await session.commit()
    await session.refresh(manuscript)
    return manuscript


def load_raw_store(settings: Settings, manuscript_id: int) -> ExtractionResult:
    """Reads back what `_write_raw_store` wrote — the structural check
    engine (V-016) is the first consumer of the full extraction (blocks,
    tables, geometry) beyond the ingestion pass itself.

    BUG-138: re-populates the local cache from durable storage first if
    Render's ephemeral disk lost it since it was written (a redeploy or
    idle-wake between ingest and this call, e.g. a resumed check run) --
    every existing caller's `except FileNotFoundError` keeps working
    unchanged for the genuinely-gone case (pre-migration rows, a purged
    manuscript)."""
    storage = get_storage(settings)
    path = ensure_local_file(settings, storage, str(raw_store_path(settings, manuscript_id)))
    return ExtractionResult.model_validate_json(path.read_text(encoding="utf-8"))


async def load_raw_store_async(settings: Settings, manuscript_id: int) -> ExtractionResult:
    """`load_raw_store`, off the event loop (backend review finding,
    BUG-138 review): every caller of `load_raw_store` runs from async
    context, and since BUG-138 a cache miss now means a real network fetch
    from durable storage, not just a local read — calling it directly, as
    every one of those sites originally did, would block the whole server
    on exactly the request pattern most likely right after a redeploy/
    idle-wake (many resumed check runs hitting a cold cache at once)."""
    return await asyncio.get_running_loop().run_in_executor(
        None, load_raw_store, settings, manuscript_id
    )


async def get_group_proposal(
    session: AsyncSession, settings: Settings, manuscript_id: int, instructor_id: int
) -> TitlePageProposal:
    """F1: source title-page proposal, distinct from the saved assignment."""
    manuscript = await session.get(Manuscript, manuscript_id)
    if (
        manuscript is None
        or manuscript.instructor_id != instructor_id
        or manuscript.origin != ManuscriptOrigin.check_upload
    ):
        raise NotFoundError(f"No manuscript with id {manuscript_id}.")

    result = await read_source(
        session, manuscript, lambda: load_raw_store_async(settings, manuscript_id)
    )
    patterns = load_patterns(settings.ingest_patterns_file)
    return extract_title_page(result, patterns)


async def _owned_current_group(session: AsyncSession, manuscript: Manuscript) -> Group | None:
    if manuscript.group_id is None:
        return None
    group = await session.scalar(
        select(Group)
        .where(Group.id == manuscript.group_id, Group.instructor_id == manuscript.instructor_id)
        .execution_options(populate_existing=True)
    )
    if group is None:
        raise NotFoundError("This manuscript's group is unavailable.")
    return group


async def _current_group_members(session: AsyncSession, group: Group) -> list[str]:
    return list(
        (
            await session.scalars(
                select(GroupMember.name)
                .where(GroupMember.group_id == group.id)
                .order_by(GroupMember.id)
            )
        ).all()
    )


async def get_manuscript_group(
    session: AsyncSession, manuscript_id: int, instructor_id: int
) -> ManuscriptGroupOut:
    manuscript = await session.scalar(
        select(Manuscript).where(
            Manuscript.id == manuscript_id,
            Manuscript.instructor_id == instructor_id,
            Manuscript.origin == ManuscriptOrigin.check_upload,
        )
    )
    if manuscript is None:
        raise NotFoundError(f"No manuscript with id {manuscript_id}.")
    group = await _owned_current_group(session, manuscript)
    return ManuscriptGroupOut(
        manuscript_id=manuscript.id,
        confirmation="recorded"
        if confirmation_recorded(
            manuscript.group_confirmation,
            manuscript.group_id,
            manuscript_id=manuscript.id,
            instructor_id=instructor_id,
        )
        else "not_recorded",
        group=CurrentGroupOut(
            id=group.id,
            name=group.name,
            member_names=await _current_group_members(session, group),
            program_id=group.program_id,
            program=await program_name_for(session, group.program_id),
            title=group.title,
        )
        if group
        else None,
    )


async def confirm_manuscript_group(
    session: AsyncSession,
    manuscript_id: int,
    instructor_id: int,
    group_name: str,
    member_names: list[str],
    program_id: int | None,
    title: str | None = None,
    *,
    expected_group_id: int | None = None,
    expected_provided: bool = False,
) -> tuple[Group, str]:
    """F1: serialize assignment, validate replay, and commit one atomic change.

    Existing group facts are retained. The receipt records submitted and
    effective identities separately because matching may union members or
    creation may disambiguate the display name.
    """
    manuscript = await session.scalar(
        select(Manuscript)
        .where(
            Manuscript.id == manuscript_id,
            Manuscript.instructor_id == instructor_id,
            Manuscript.origin == ManuscriptOrigin.check_upload,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if manuscript is None:
        raise NotFoundError(f"No manuscript with id {manuscript_id}.")
    if not group_name.strip():
        raise FileMalformedError("Enter a group name before confirming.")
    validate_group_fields(group_name, member_names, title)
    if program_id is not None and (type(program_id) is not int or program_id <= 0):
        raise FileMalformedError("Choose a valid program, or leave it as Not set.")
    if (
        expected_provided
        and expected_group_id is not None
        and (type(expected_group_id) is not int or expected_group_id <= 0)
    ):
        raise FileMalformedError("The expected group assignment is invalid. Reopen Group details.")
    values = assignment_values(group_name, member_names, program_id, title)
    request = AssignmentRequest(
        **values.model_dump(),
        expected_group_id=expected_group_id,
        expected_provided=expected_provided,
    )
    current = await _owned_current_group(session, manuscript)
    receipt = current_receipt(manuscript.group_confirmation, manuscript.group_id)
    # A response can be lost after commit: the original expected ID may now
    # be stale, but an exact validated request replay must remain a no-op.
    if receipt is not None and receipt.request == request and current is not None:
        await session.commit()
        return current, "unchanged"
    if expected_provided and manuscript.group_id != expected_group_id:
        raise ConflictError(
            "This manuscript's group assignment changed. "
            "Close and reopen Group details before trying again."
        )
    if (
        receipt is not None
        and current is not None
        and expected_provided
        and values.model_dump()
        == receipt.request.model_dump(exclude={"expected_group_id", "expected_provided"})
    ):
        await session.commit()
        return current, "unchanged"
    if (
        current is not None
        and expected_provided
        and values
        == assignment_values(
            current.name,
            await _current_group_members(session, current),
            current.program_id,
            current.title,
        )
    ):
        await session.commit()
        return current, "unchanged"
    if program_id is not None and await session.get(Program, program_id) is None:
        raise NotFoundError(f"No program with id {program_id}.")
    group, matched = await match_or_create_group_from_proposal(
        session, instructor_id, group_name, member_names
    )
    if not matched:
        group.program_id = program_id
        group.title = values.title
    manuscript.group_id = group.id
    manuscript.group_label = group.name
    effective = assignment_values(
        group.name, await _current_group_members(session, group), group.program_id, group.title
    )
    manuscript.group_confirmation = AssignmentReceipt(
        version=1, assigned_group_id=group.id, request=request, effective=effective
    ).model_dump(mode="json")
    await session.commit()
    return group, "matched" if matched else "created"
