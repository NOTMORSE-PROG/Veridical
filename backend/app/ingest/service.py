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
from app.errors import FileMalformedError, FileTooLargeError
from app.ingest import docx, pdf, references, vision
from app.ingest.patterns import load_patterns
from app.ingest.schemas import ExtractionResult
from app.llm import LLMNotConfiguredError, get_llm_client
from app.models.citation import Citation
from app.models.enums import IngestStatus
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript

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
        except LLMNotConfiguredError:
            # No client (real mode before V-009): image content stays
            # unread — an honest state, never an ingestion failure.
            result.vision_status = "unavailable"
        await loop.run_in_executor(
            None, _write_raw_store, result, raw_store_path(settings, manuscript.id)
        )
    except Exception:
        # Stage boundary (CODING.md §2): the failure is recorded on the row,
        # then propagates — run-level stage bookkeeping arrives with V-018.
        manuscript.ingest_status = IngestStatus.failed
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
) -> tuple[Manuscript, ExtractionResult, int]:
    """Full upload flow for the HTTP surface: save (size-capped) → own row
    → ingest. Uploads attach to the demo instructor until auth lands
    (V-014) — this endpoint is the dev/demo surface for screen 4f."""
    settings = settings or get_settings()
    instructor = await session.scalar(select(Instructor).order_by(Instructor.id).limit(1))
    if instructor is None:
        # A fresh database with no seed: create the demo owner rather than
        # failing the very first upload of a demo session.
        instructor = Instructor(email="instructor@demo.local", display_name="Demo Instructor")
        session.add(instructor)
        await session.commit()

    manuscript = Manuscript(instructor_id=instructor.id, group_label=group_label, file_ref="")
    session.add(manuscript)
    await session.commit()

    # Stored under a server-owned name: the id is the identity; the
    # original filename is user input and never touches the filesystem.
    suffix = Path(filename).suffix.lower()[:8]
    dest = settings.data_dir / "uploads" / f"{manuscript.id}{suffix}"
    try:
        await save_upload(chunks, dest, settings)
    except FileTooLargeError:
        manuscript.ingest_status = IngestStatus.failed
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


def load_raw_store(settings: Settings, manuscript_id: int) -> ExtractionResult:
    """Reads back what `_write_raw_store` wrote — the structural check
    engine (V-016) is the first consumer of the full extraction (blocks,
    tables, geometry) beyond the ingestion pass itself."""
    return ExtractionResult.model_validate_json(
        raw_store_path(settings, manuscript_id).read_text(encoding="utf-8")
    )
