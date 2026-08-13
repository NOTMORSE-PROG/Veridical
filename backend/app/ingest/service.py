"""Ingestion orchestration: file → extraction → DB row + raw store.

The ingest stage of the pipeline (ENGINEERING.md §4). Extraction itself is
CPU-bound and runs in the default threadpool; this module owns the status
transitions on the manuscript row and the raw-store write.
"""

import asyncio
import zipfile
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import messages
from app.config import Settings, get_settings
from app.errors import ApiDownError, FileMalformedError, FileTooLargeError, QuotaExhaustedError
from app.ingest import docx, pdf, references, vision
from app.ingest.patterns import load_patterns
from app.ingest.schemas import ExtractionResult, ManuscriptListItem, PaginatedManuscripts
from app.llm import LLMNotConfiguredError, get_llm_client
from app.models.citation import Citation
from app.models.enums import CheckRunStatus, IngestFailureReason, IngestStatus
from app.models.manuscript import Manuscript
from app.models.run import CheckRun

# One extractor per supported suffix. Each is a sync callable executed off
# the event loop. Legacy .doc is deliberately absent: rejected with a clear
# user-fixable message (V-005 edge case).
EXTRACTORS: dict[str, Callable[[str, Settings], ExtractionResult]] = {
    ".pdf": pdf.extract_document,
    ".docx": docx.extract_document,
}


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


def detect_format(file_path: Path) -> str:
    """Sniff the REAL type from content — extensions are user input and
    lie (a DOCX renamed to .pdf must still parse as a DOCX, V-008 edge
    case). Returns an EXTRACTORS key or the honest non-key (".doc") so
    select_extractor can name what was actually uploaded."""
    with file_path.open("rb") as fh:
        head = fh.read(len(_OLE_MAGIC))
    if head.startswith(_PDF_MAGIC):
        return ".pdf"
    if head.startswith(_ZIP_MAGIC):
        try:
            with zipfile.ZipFile(file_path) as zf:
                if any(name.startswith("word/") for name in zf.namelist()):
                    return ".docx"
        except zipfile.BadZipFile:
            pass
        raise FileMalformedError(messages.FILE_UNREADABLE)
    if head.startswith(_OLE_MAGIC):
        return ".doc"  # legacy Word: recognized, then rejected by name
    raise FileMalformedError(messages.FILE_UNREADABLE)


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


async def ingest_manuscript(
    session: AsyncSession,
    manuscript: Manuscript,
    file_path: Path,
    settings: Settings | None = None,
) -> ExtractionResult:
    settings = settings or get_settings()
    manuscript.ingest_status = IngestStatus.processing
    await session.commit()
    loop = asyncio.get_running_loop()
    try:
        # Dispatch by sniffed content (extensions lie) — inside the stage
        # boundary so an unreadable file leaves the row `failed`, not stuck.
        extractor = select_extractor(detect_format(file_path))
        result = await loop.run_in_executor(None, extractor, str(file_path), settings)
        patterns = load_patterns(settings.ingest_patterns_file)
        drafts = await loop.run_in_executor(None, references.extract_references, result, patterns)
        try:
            # Vision pass (F1.3) merges image tables/equations into result
            # BEFORE the raw store is written, so the store is complete.
            await vision.read_images(
                result, file_path, get_llm_client(settings), patterns, settings
            )
        except (LLMNotConfiguredError, ApiDownError, QuotaExhaustedError):
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
        await loop.run_in_executor(
            None, _write_raw_store, result, raw_store_path(settings, manuscript.id)
        )
    except FileMalformedError:
        # Stage boundary (CODING.md §2): the failure is recorded on the row,
        # then propagates — run-level stage bookkeeping arrives with V-018.
        # BUG-016: the row must say why, not just that it failed.
        manuscript.ingest_status = IngestStatus.failed
        manuscript.ingest_failure_reason = IngestFailureReason.unreadable_format
        await session.commit()
        raise
    except Exception:
        manuscript.ingest_status = IngestStatus.failed
        manuscript.ingest_failure_reason = IngestFailureReason.extraction_failed
        await session.commit()
        raise

    # Re-ingest replaces the previous citation set (idempotent).
    await session.execute(delete(Citation).where(Citation.manuscript_id == manuscript.id))
    session.add_all(Citation(manuscript_id=manuscript.id, **asdict(draft)) for draft in drafts)
    manuscript.section_tree = result.section_tree.model_dump()
    manuscript.ingest_status = IngestStatus.done
    await session.commit()
    return result


async def ingest_upload(
    session: AsyncSession,
    chunks: AsyncIterator[bytes],
    filename: str,
    group_label: str,
    settings: Settings | None = None,
    *,
    instructor_id: int,
) -> tuple[Manuscript, ExtractionResult, int]:
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
    # rejects NUL outright; backend-critic finding, BUG-022 review).
    # `[:255]` matches the column's own limit so an unusually long
    # filename never 500s here either.
    clean_name = "".join(ch for ch in filename.replace("\\", "/") if ch.isprintable())
    original_filename = Path(clean_name).name[:255] or None
    manuscript = Manuscript(
        instructor_id=instructor_id,
        group_label=group_label,
        file_ref="",
        original_filename=original_filename,
    )
    session.add(manuscript)
    await session.commit()

    # Stored under a server-owned name: the id is the identity; the
    # original filename never touches the filesystem (it's persisted for
    # display only, above).
    suffix = Path(filename).suffix.lower()[:8]
    dest = settings.data_dir / "uploads" / f"{manuscript.id}{suffix}"
    try:
        await save_upload(chunks, dest, settings)
    except FileTooLargeError:
        manuscript.ingest_status = IngestStatus.failed
        manuscript.ingest_failure_reason = IngestFailureReason.file_too_large
        await session.commit()
        raise
    manuscript.file_ref = str(dest)
    await session.commit()
    result = await ingest_manuscript(session, manuscript, dest, settings)
    n_citations = (
        await session.scalar(
            select(func.count())
            .select_from(Citation)
            .where(Citation.manuscript_id == manuscript.id)
        )
        or 0
    )
    return manuscript, result, n_citations


def _write_raw_store(result: ExtractionResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(), encoding="utf-8")


async def list_manuscripts(
    session: AsyncSession, instructor_id: int, *, page: int = 1, page_size: int = 50
) -> PaginatedManuscripts:
    """Server-paginated listing (V-021 edge case: 100+ manuscripts in
    defense season) for both the dashboard table (4e) and the New Check
    modal's manuscript picker (V-018, which just requests a generously
    large page). Each row carries its own latest check_run id/status so
    the dashboard can link "view progress"/"open report" with zero extra
    per-row requests."""
    total = await session.scalar(
        select(func.count())
        .select_from(Manuscript)
        .where(Manuscript.instructor_id == instructor_id)
    )
    manuscripts = (
        await session.scalars(
            select(Manuscript)
            .where(Manuscript.instructor_id == instructor_id)
            .order_by(Manuscript.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    manuscript_ids = [m.id for m in manuscripts]
    latest_by_manuscript: dict[int, CheckRun] = {}
    # Separate from `latest_by_manuscript` (backend-critic finding,
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
                .order_by(CheckRun.manuscript_id, CheckRun.created_at.desc())
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

    items = [
        ManuscriptListItem(
            id=m.id,
            group_label=m.group_label,
            original_filename=m.original_filename,
            ingest_status=m.ingest_status.value,
            ingest_failure_reason=(
                m.ingest_failure_reason.value if m.ingest_failure_reason else None
            ),
            created_at=m.created_at,
            latest_check_run_id=_latest_id(m.id),
            latest_check_run_status=_latest_status(m.id),
            latest_done_check_run_id=_latest_done_id(m.id),
        )
        for m in manuscripts
    ]
    return PaginatedManuscripts(items=items, total=total or 0, page=page, page_size=page_size)


def load_raw_store(settings: Settings, manuscript_id: int) -> ExtractionResult:
    """Reads back what `_write_raw_store` wrote — the structural check
    engine (V-016) is the first consumer of the full extraction (blocks,
    tables, geometry) beyond the ingestion pass itself."""
    return ExtractionResult.model_validate_json(
        raw_store_path(settings, manuscript_id).read_text(encoding="utf-8")
    )
