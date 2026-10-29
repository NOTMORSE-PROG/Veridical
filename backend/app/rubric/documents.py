"""Owned required-format originals and fresh, bounded local reading views."""

import asyncio
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from zipfile import BadZipFile

from docx.opc.exceptions import PackageNotFoundError
from lxml.etree import XMLSyntaxError
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import (
    ConflictError,
    FileMalformedError,
    FileTooLargeError,
    GoneError,
    NotFoundError,
    SourceReadUnavailableError,
)
from app.ingest.admission import preparation_scope
from app.ingest.docx import extract_document
from app.ingest.docx_source import read_docx_image
from app.ingest.schemas import ExtractionResult
from app.ingest.service import detect_format, hash_file_sha256
from app.models.rubric import Rubric
from app.report.document_projection import project_docx_source
from app.report.schemas import DocumentParagraphsOut
from app.storage import ensure_local_file, get_storage


class RubricDocumentOut(BaseModel):
    rubric_id: int
    rubric_family_id: uuid.UUID
    version: int
    available: bool
    source_format: Literal["pdf", "docx"] | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    unavailable_reason: Literal["not_available_locally"] | None = None
    reconstruction: Literal["fresh_local_source", "original_pdf"] | None = None


class RubricParagraphsOut(RubricDocumentOut):
    content: DocumentParagraphsOut


@dataclass(frozen=True)
class RubricSource:
    id: int
    family_id: uuid.UUID
    version: int
    source_file: str | None


async def read_rubric_document[T](
    session: AsyncSession,
    rubric_id: int,
    instructor_id: int,
    settings: Settings,
    reader: Callable[[RubricSource, Settings], T],
) -> T:
    rubric = await session.scalar(
        select(Rubric)
        .where(Rubric.id == rubric_id, Rubric.instructor_id == instructor_id)
        .with_for_update(read=True)
    )
    if rubric is None:
        raise NotFoundError("Required format not found.")
    source = RubricSource(rubric.id, rubric.rubric_family_id, rubric.version, rubric.source_file)
    # Admission remains held by actual native work if its awaiter times out.
    with preparation_scope(settings) as lease:
        try:
            return await asyncio.wait_for(
                lease.run(reader, source, settings), settings.ingest_extraction_timeout_seconds
            )
        except TimeoutError:
            raise SourceReadUnavailableError(
                "The format reading view did not finish in time. Try again later."
            ) from None
        except OSError:
            raise SourceReadUnavailableError(
                "The stored format could not be read. Try again or use another copy."
            ) from None


def _source_path(source: RubricSource, settings: Settings) -> Path:
    if not source.source_file:
        raise FileNotFoundError
    recorded = Path(source.source_file)
    expected_root = (settings.data_dir / settings.rubric_upload_subdir).resolve()
    # Uploads retain a shortened, potentially misleading suffix. Sniff bytes
    # later; the leaf binds ownership, not the user-provided format name.
    suffix = recorded.suffix
    if recorded.name != f"{source.id}{suffix}" or any(c in recorded.name for c in ":\x00"):
        raise GoneError("The original file for this version is unavailable locally.")
    path = recorded.resolve()
    if recorded.is_symlink() or path.parent != expected_root or path.name != recorded.name:
        raise GoneError("The original file for this version is unavailable locally.")
    if not path.is_file():
        storage = get_storage(settings)
        try:
            ensure_local_file(settings, storage, str(recorded))
        finally:
            storage.close()
    if path.stat().st_size > settings.max_upload_mb * 1024 * 1024:
        raise FileTooLargeError("The stored format exceeds the supported file size.")
    return path


def document_metadata(source: RubricSource, settings: Settings) -> RubricDocumentOut:
    base = dict(rubric_id=source.id, rubric_family_id=source.family_id, version=source.version)
    try:
        path = _source_path(source, settings)
    except FileNotFoundError:
        return RubricDocumentOut(
            **base, available=False, unavailable_reason="not_available_locally"
        )
    actual_format = detect_format(path, settings)
    if actual_format not in (".pdf", ".docx"):
        raise FileMalformedError("This stored format cannot be displayed as PDF or DOCX.")
    return RubricDocumentOut(
        **base,
        available=True,
        source_format=actual_format.removeprefix("."),
        source_sha256=hash_file_sha256(path, settings.content_hash_read_chunk_bytes),
        reconstruction="fresh_local_source" if actual_format == ".docx" else "original_pdf",
    )


def _bound_source(
    source: RubricSource, settings: Settings, expected_hash: str
) -> tuple[Path, RubricDocumentOut]:
    if re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None:
        raise ConflictError("The format source identity is unavailable. Reload this version.")
    metadata = document_metadata(source, settings)
    if not metadata.available:
        raise GoneError("The original file for this version is unavailable locally.")
    if metadata.source_sha256 != expected_hash:
        raise ConflictError("The stored format changed. Reload its reading view before continuing.")
    return _source_path(source, settings), metadata


def document_file(
    source: RubricSource, settings: Settings, expected_hash: str, *, pdf_only: bool = False
) -> tuple[Path, str, str]:
    path, metadata = _bound_source(source, settings, expected_hash)
    if pdf_only and metadata.source_format != "pdf":
        raise ConflictError("This format is not an original PDF.")
    mime = (
        "application/pdf"
        if metadata.source_format == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return path, mime, f"required-format-{source.id}-v{source.version}.{metadata.source_format}"


def _extract_docx(path: Path, settings: Settings, expected_hash: str) -> ExtractionResult:
    try:
        extraction = extract_document(str(path), settings)
    except (
        BadZipFile,
        PackageNotFoundError,
        XMLSyntaxError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise FileMalformedError("The stored DOCX reading view could not be prepared.") from None
    if extraction.docx_source_sha256 != expected_hash:
        raise ConflictError("The stored format changed. Reload its reading view before continuing.")
    return extraction


def document_paragraphs(
    source: RubricSource, settings: Settings, expected_hash: str
) -> RubricParagraphsOut:
    path, metadata = _bound_source(source, settings, expected_hash)
    if metadata.source_format != "docx":
        raise ConflictError("This format does not have a DOCX reading view.")
    extraction = _extract_docx(path, settings, expected_hash)
    return RubricParagraphsOut(
        **metadata.model_dump(),
        content=project_docx_source(extraction, expected_hash, extraction.section_tree, settings),
    )


def document_image(
    source: RubricSource, settings: Settings, expected_hash: str, image_index: int
) -> tuple[bytes, str]:
    if image_index < 0 or image_index >= settings.docx_image_max_count:
        raise GoneError("This drawing is not available in the format reading view.")
    path, metadata = _bound_source(source, settings, expected_hash)
    if metadata.source_format != "docx":
        raise ConflictError("This format does not have DOCX images.")
    extraction = _extract_docx(path, settings, expected_hash)
    return read_docx_image(path, extraction, image_index, expected_hash, settings)
