"""Byte-derived source identity after caller authorization and storage fencing."""

from pathlib import Path
from typing import Literal

import pymupdf

from app import messages
from app.config import Settings
from app.errors import ConflictError, FileMalformedError, GoneError
from app.ingest.service import detect_format
from app.models.manuscript import Manuscript
from app.storage import ensure_local_file, get_storage

SourceFormat = Literal["pdf", "docx"]


def manuscript_source_file(manuscript: Manuscript, settings: Settings) -> tuple[Path, SourceFormat]:
    """Read original identity only; never extract or change retained assessment.

    Callers authorize and hold read_source's retention fence. Storage outages
    retain their original exception; an absent object is distinct from an
    unreadable or unsupported original. A filename is never a type authority.
    """
    if manuscript.purged_at is not None:
        raise GoneError("This manuscript's source file was purged and can no longer be read.")
    try:
        path = ensure_local_file(settings, get_storage(settings), manuscript.file_ref)
        suffix = detect_format(path, settings)
    except FileNotFoundError:
        raise GoneError(messages.MANUSCRIPT_SOURCE_MISSING) from None
    except FileMalformedError:
        raise ConflictError("This manuscript's source file could not be read.") from None
    if suffix == ".pdf":
        return path, "pdf"
    if suffix == ".docx":
        return path, "docx"
    raise ConflictError("This manuscript's source file format isn't supported for viewing.")


def open_source_pdf(path: Path) -> pymupdf.Document:
    """Successful native open alone also accepts non-PDF document formats."""
    doc = None
    try:
        doc = pymupdf.open(path)
        if not doc.is_pdf or doc.needs_pass or doc.page_count < 1:
            raise ValueError("Source is not a readable PDF")
        return doc
    except (RuntimeError, ValueError, OSError):
        if doc is not None:
            doc.close()
    # Native constructor tracebacks can retain an open stream. Leave the
    # handler before raising so the domain error does not keep that context.
    raise ConflictError("This manuscript's source file could not be opened as a PDF.") from None


def readable_source_file(manuscript: Manuscript, settings: Settings) -> tuple[Path, SourceFormat]:
    path, source_format = manuscript_source_file(manuscript, settings)
    if source_format == "pdf":
        with open_source_pdf(path):
            pass
    return path, source_format
