"""F8: qualified PDF text-layer navigation on one pinned source snapshot.

Matching text is not visible-ink verification, academic support or a grade.
No source document is edited. This module does not authorize source access.
"""

import hashlib
import re
import struct
from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite
from typing import Literal

import pymupdf
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.errors import ConflictError
from app.ingest.normalize import normalize
from app.ingest.regions import recover_region
from app.ingest.schemas import SectionTree
from app.report.evidence_references import EvidenceSourceReference
from app.report.export import _LLM_MODE_DISCLOSURE
from app.report.page_image_disclosure import PageImageUnavailable, disclosure_layout
from app.report.page_images import _render_page_png
from app.report.source_files import open_source_pdf_bytes

Bbox = tuple[float, float, float, float]


class EvidencePlacementOut(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    reference_id: str
    state: Literal["text_layer_match", "page_only", "paragraph_only", "unavailable"]
    page: int | None = Field(default=None, gt=0)
    paragraph: int | None = Field(default=None, ge=0)
    # Actual PDF coordinates, converted through the source transformation matrix.
    boxes: tuple[Bbox, ...] = ()
    reason: (
        Literal[
            "assessment_revision_unverified",
            "source_identity_unverified",
            "bounded_excerpt_only",
            "source_changed",
            "source_mutated",
            "location_unavailable",
            "scope_first_page",
            "no_saved_quote",
            "quote_unlocated_or_repeated",
            "quote_geometry_unverified",
            "resource_limit",
            "public_page_annotations",
            "public_page_inspection_unavailable",
        ]
        | None
    ) = None


@dataclass(frozen=True)
class ReferencePageImage:
    """Internal image result; callers publish only after their authorization recheck."""

    placement: EvidencePlacementOut
    image_png: bytes | None


@dataclass(frozen=True)
class PinnedPdfSource:
    """Owned native handle, valid only inside pin_pdf_source's read-only context."""

    document: pymupdf.Document
    source_sha256: str
    source_bytes: bytes


@contextmanager
def pin_pdf_source(data: bytes, expected_sha256: str, settings: Settings):
    pinned = bytes(data)
    if len(pinned) > settings.max_upload_mb * 1024 * 1024:
        raise ConflictError("The source exceeds the evidence-reading limit.")
    digest = hashlib.sha256(pinned).hexdigest()
    if digest != expected_sha256:
        raise ConflictError("The manuscript source no longer matches this evidence record.")
    doc = open_source_pdf_bytes(pinned)
    try:
        if doc.page_count > settings.ingest_max_pages:
            raise ConflictError("The source exceeds the evidence-reading page limit.")
        yield PinnedPdfSource(doc, digest, pinned)
    finally:
        doc.close()


def _unique(text: str, quote: str) -> bool:
    first = text.find(quote)
    return first >= 0 and text.find(quote, first + 1) < 0


def locate_pdf_evidence(
    source: PinnedPdfSource,
    reference: EvidenceSourceReference,
    section_tree: SectionTree,
    settings: Settings,
) -> EvidencePlacementOut:
    reference = EvidenceSourceReference.model_validate(reference.model_dump())
    identity = {"reference_id": reference.reference_id}
    if reference.source_state != "not_checked":
        return EvidencePlacementOut(**identity, state="unavailable", reason=reference.source_state)
    if source.source_sha256 != reference.source_sha256:
        return EvidencePlacementOut(**identity, state="unavailable", reason="source_changed")
    doc = source.document
    if doc.is_closed or doc.is_dirty:
        return EvidencePlacementOut(**identity, state="unavailable", reason="source_mutated")
    # Empty text deliberately disables legacy prefix recovery. Only an explicit
    # page receives the independent full-quote verification below.
    anchor = reference.anchor or ""
    explicit_page = re.fullmatch(r"(?:page|p\.)\s*([0-9]+)", anchor.strip(), re.IGNORECASE)
    # Criterion evidence and existing source controls also use "page N". Map
    # only that complete protocol form to the legacy recovery representation.
    if explicit_page:
        anchor = f"p. {int(explicit_page[1])}"
    region = recover_region(doc, section_tree, anchor, "", settings=settings)
    if region.page is None or not (1 <= region.page <= doc.page_count):
        return EvidencePlacementOut(**identity, state="unavailable", reason="location_unavailable")
    page = doc[region.page - 1]
    if reference.audience == "public":
        try:
            # Includes annotations, widgets AND links; never suppress an overlay
            # and expose content that an unapplied redaction may have covered.
            annotations = page.annot_xrefs()
            if not isinstance(annotations, list):
                raise ValueError("Incomplete annotation inspection")
            if annotations:
                return EvidencePlacementOut(
                    **identity, state="unavailable", reason="public_page_annotations"
                )
        except (RuntimeError, ValueError, TypeError, AttributeError):
            return EvidencePlacementOut(
                **identity, state="unavailable", reason="public_page_inspection_unavailable"
            )
    coarse = {**identity, "state": "page_only", "page": region.page}
    if region.kind != "page_only":
        return EvidencePlacementOut(**coarse, reason="scope_first_page")
    quote = normalize(reference.quote)
    if not quote:
        return EvidencePlacementOut(**coarse, reason="no_saved_quote")
    if len(quote) < settings.region_search_min_candidate_chars:
        return EvidencePlacementOut(**coarse, reason="quote_unlocated_or_repeated")
    # PyMuPDF's public text extraction temporarily writes and restores /Rotate.
    # Use a disposable extraction handle so the pinned rendering handle stays
    # clean and its mutation guard continues to detect caller-side changes.
    try:
        with open_source_pdf_bytes(source.source_bytes) as extraction:
            return _locate_quote(extraction[region.page - 1], quote, coarse, settings)
    except (ConflictError, RuntimeError, ValueError, TypeError, AttributeError):
        return EvidencePlacementOut(**coarse, reason="quote_geometry_unverified")


def _locate_quote(page, quote, coarse, settings):
    try:
        text = normalize(page.get_text("text"))
        if len(text) > settings.pdf_page_text_max_chars:
            return EvidencePlacementOut(**coarse, reason="resource_limit")
        if not _unique(text.casefold(), quote.casefold()):
            return EvidencePlacementOut(**coarse, reason="quote_unlocated_or_repeated")
        quads = page.search_for(quote, quads=True)
        if not quads or len(quads) > settings.evidence_source_max_highlight_boxes:
            return EvidencePlacementOut(**coarse, reason="quote_geometry_unverified")
        bounds = page.rect * page.derotation_matrix
        inverse = ~page.transformation_matrix
        boxes = []
        portions = []
        for quad in quads:
            native = quad.rect
            if (
                native.is_empty
                or not all(isfinite(value) for value in native)
                or not bounds.contains(native)
            ):
                return EvidencePlacementOut(**coarse, reason="quote_geometry_unverified")
            portions.append(page.get_textbox(native))
            converted = native * inverse
            if converted.is_empty or not all(isfinite(value) for value in converted):
                return EvidencePlacementOut(**coarse, reason="quote_geometry_unverified")
            boxes.append(tuple(float(value) for value in converted))
        if normalize(" ".join(portions)).casefold() != quote.casefold():
            return EvidencePlacementOut(**coarse, reason="quote_geometry_unverified")
        return EvidencePlacementOut(
            reference_id=coarse["reference_id"],
            state="text_layer_match",
            page=coarse["page"],
            boxes=tuple(boxes),
        )
    except (RuntimeError, ValueError, TypeError, AttributeError):
        return EvidencePlacementOut(**coarse, reason="quote_geometry_unverified")


def render_pdf_evidence(
    source: PinnedPdfSource,
    reference: EvidenceSourceReference,
    section_tree: SectionTree,
    settings: Settings,
    *,
    llm_mode: str,
) -> ReferencePageImage:
    """Reuse the native rasterizer with this reference's own admitted marks only.

    No cache is consulted. In particular private annotated images cannot satisfy
    public requests; the public page policy is inspected on this pinned document.
    """
    placement = locate_pdf_evidence(source, reference, section_tree, settings)
    if placement.state == "unavailable" or placement.page is None:
        return ReferencePageImage(placement, None)
    mode = llm_mode if llm_mode in {"fake", "real", "unknown"} else "unknown"
    notice = _LLM_MODE_DISCLOSURE.get(mode)
    disclosure = (
        (disclosure_layout()["summaries"].get(mode, notice[0]), notice[1])
        if notice is not None
        else None
    )
    try:
        render_settings = settings.model_copy(
            update={
                "export_page_image_max_pixels": min(
                    settings.export_page_image_max_pixels,
                    settings.evidence_source_max_rendered_pixels,
                )
            }
        )
        image = _render_page_png(
            source.document[placement.page - 1],
            placement.boxes if placement.state == "text_layer_match" else (),
            llm_disclosure=disclosure,
            settings=render_settings,
            box_coordinates="pdf_points",
        )
    except PageImageUnavailable:
        return ReferencePageImage(
            EvidencePlacementOut(
                reference_id=reference.reference_id, state="unavailable", reason="resource_limit"
            ),
            None,
        )
    # Apply response/batch caps as well as the rasterizer's per-page cap. PNG
    # IHDR contains the final dimensions, including any detached mode notice.
    width, height = struct.unpack(">II", image[16:24])
    if (
        len(image) > settings.evidence_source_max_rendered_bytes
        or width * height > settings.evidence_source_max_rendered_pixels
    ):
        return ReferencePageImage(
            EvidencePlacementOut(
                reference_id=reference.reference_id, state="unavailable", reason="resource_limit"
            ),
            None,
        )
    return ReferencePageImage(placement, image)
