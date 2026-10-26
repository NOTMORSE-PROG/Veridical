"""F8/4w: a private reader copy tied to the exact source used for navigation.

Source identity is independent of an instructor's grading revision. It grants
no access. Callers authorize, retain read_source and drain admitted native work.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.config import Settings
from app.errors import ConflictError, NotFoundError
from app.ingest.docx_source import read_docx_image
from app.ingest.regions import recover_region
from app.ingest.schemas import ExtractionResult, SectionTree
from app.ingest.service import raw_store_path
from app.report.document_projection import project_docx_source
from app.report.evidence_images import EvidenceSourceFile, read_bounded_source_bytes
from app.report.evidence_placement import EvidencePlacementOut, locate_pdf_evidence, pin_pdf_source
from app.report.evidence_references import EvidenceSourceReference
from app.report.schemas import DocumentParagraphsOut
from app.report.source_files import SourceFormat, manuscript_source_file
from app.storage import ensure_local_file, get_storage


@dataclass(frozen=True)
class BoundEvidenceSource:
    check_run_id: int
    manuscript_id: int
    source_format: SourceFormat
    source_identity: str
    source_sha256: str
    pdf_bytes: bytes | None = None
    document: DocumentParagraphsOut | None = None
    docx_path: Path | None = None
    extraction: ExtractionResult | None = None


def _source_identity(run_id, manuscript_id, source_format, digest, document):
    canonical = json.dumps(
        {
            "schema": "evidence-reader-v1",
            "check_run_id": run_id,
            "manuscript_id": manuscript_id,
            "source_format": source_format,
            "source_sha256": digest,
            "document": document.model_dump(mode="json") if document is not None else None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def prepare_bound_evidence_source(
    source_file: EvidenceSourceFile,
    check_run_id: int,
    manuscript_id: int,
    expected_sha256: str | None,
    section_tree: SectionTree,
    settings: Settings,
) -> BoundEvidenceSource:
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ConflictError(
            "The saved source identity is unavailable. Source marks cannot be bound."
        )
    path, source_format = manuscript_source_file(source_file, settings)
    data = read_bounded_source_bytes(path, settings)
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ConflictError("The available source no longer matches this evidence record.")
    if source_format == "pdf":
        with pin_pdf_source(data, expected_sha256, settings):
            pass
        return BoundEvidenceSource(
            check_run_id,
            manuscript_id,
            source_format,
            _source_identity(check_run_id, manuscript_id, source_format, expected_sha256, None),
            expected_sha256,
            pdf_bytes=data,
        )
    limit = settings.evidence_source_max_extraction_bytes
    try:
        extraction_path = ensure_local_file(
            settings, get_storage(settings), str(raw_store_path(settings, manuscript_id))
        )
        if extraction_path.stat().st_size > limit:
            raise ConflictError("The saved source text exceeds the evidence-reading limit.")
        with extraction_path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        raise ConflictError(
            "The saved source text is unavailable for evidence navigation."
        ) from None
    if len(raw) > limit:
        raise ConflictError("The saved source text exceeds the evidence-reading limit.")
    try:
        extraction = ExtractionResult.model_validate_json(raw)
        if (
            extraction.anchor_kind != "paragraph"
            or extraction.docx_source_sha256 != expected_sha256
        ):
            raise ConflictError("The saved extracted text could not be linked to this source copy.")
        document = project_docx_source(extraction, expected_sha256, section_tree, settings)
        identity = _source_identity(
            check_run_id, manuscript_id, source_format, expected_sha256, document
        )
    except (ValidationError, ValueError, TypeError):
        raise ConflictError(
            "The saved source text could not be read for evidence navigation."
        ) from None
    return BoundEvidenceSource(
        check_run_id,
        manuscript_id,
        source_format,
        identity,
        expected_sha256,
        document=document,
        docx_path=path,
        extraction=extraction,
    )


def read_bound_docx_image(source: BoundEvidenceSource, image_index: int, settings: Settings):
    if (
        source.source_format != "docx"
        or source.document is None
        or source.docx_path is None
        or source.extraction is None
    ):
        raise ConflictError("This source copy is not an extracted DOCX document.")
    if not any(
        image.image_index == image_index and image.available for image in source.document.images
    ):
        raise NotFoundError("No available image at this recorded source location.")
    return read_docx_image(
        source.docx_path, source.extraction, image_index, source.source_sha256, settings
    )


def locate_bound_evidence(
    source: BoundEvidenceSource,
    reference: EvidenceSourceReference,
    section_tree: SectionTree,
    settings: Settings,
) -> EvidencePlacementOut:
    reference = EvidenceSourceReference.model_validate(reference.model_dump())
    if (
        reference.audience != "private"
        or reference.check_run_id != source.check_run_id
        or reference.manuscript_id != source.manuscript_id
    ):
        raise ConflictError("The selected evidence belongs to a different source copy.")
    if reference.source_state != "not_checked":
        return EvidencePlacementOut(
            reference_id=reference.reference_id, state="unavailable", reason=reference.source_state
        )
    if (
        reference.source_sha256 != source.source_sha256
        or reference.source_scope != "own_manuscript"
    ):
        raise ConflictError("The selected evidence belongs to a different source copy.")
    if source.source_format == "pdf":
        with pin_pdf_source(source.pdf_bytes, source.source_sha256, settings) as pinned:
            return locate_pdf_evidence(pinned, reference, section_tree, settings)
    anchor = reference.anchor or ""
    paragraph = re.fullmatch(r"paragraph\s+(\d+)", anchor.strip(), flags=re.IGNORECASE)
    if paragraph:
        anchor = "¶" + paragraph[1]
    region = recover_region(None, section_tree, anchor, "", settings=settings)
    document = source.document
    ordinals = (
        (
            {row.paragraph for row in document.paragraphs}
            | {row.paragraph for row in document.tables}
            | {row.paragraph for row in document.images}
        )
        if document is not None
        else set()
    )
    if region.kind == "paragraph_only" and region.paragraph in ordinals:
        return EvidencePlacementOut(
            reference_id=reference.reference_id, state="paragraph_only", paragraph=region.paragraph
        )
    return EvidencePlacementOut(
        reference_id=reference.reference_id, state="unavailable", reason="location_unavailable"
    )
