"""Authorized original DOCX image access; no assessment on source navigation."""

from pathlib import Path

from pydantic import ValidationError

from app.config import Settings
from app.errors import ConflictError, GoneError, NotFoundError
from app.ingest.docx_source import _verify_occurrence as _verify_occurrence
from app.ingest.docx_source import read_docx_image
from app.ingest.service import load_raw_store
from app.models.manuscript import Manuscript
from app.storage import ensure_local_file, get_storage


def manuscript_docx_image_for(
    manuscript: Manuscript, image_index: int, settings: Settings
) -> tuple[bytes, str]:
    """Caller must authorize before requesting private source bytes."""
    if manuscript.purged_at is not None:
        raise GoneError("This manuscript's source file was purged and can no longer be read.")
    if Path(manuscript.file_ref).suffix.lower() != ".docx":
        raise ConflictError("This manuscript's source file isn't a DOCX.")
    if image_index < 0 or image_index >= settings.docx_image_max_count:
        raise NotFoundError("No image at this source location.")
    try:
        extraction = load_raw_store(settings, manuscript.id)
    except (FileNotFoundError, ValidationError):
        raise GoneError(
            "This source image could not be verified against the original DOCX."
        ) from None
    if image_index >= len(extraction.images):
        raise NotFoundError("No image at this source location.")
    if (
        not extraction.docx_source_sha256
        or extraction.docx_source_sha256 != manuscript.content_hash
        or extraction.images[image_index].docx_source is None
    ):
        raise GoneError("This source image could not be verified against the original DOCX.")
    path = ensure_local_file(settings, get_storage(settings), manuscript.file_ref)
    return read_docx_image(path, extraction, image_index, manuscript.content_hash, settings)
