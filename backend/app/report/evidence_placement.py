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
from pathlib import Path
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
from app.report.page_images import _render_page_png, planned_page_image_pixels
from app.report.source_files import open_source_pdf, open_source_pdf_bytes

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
    # Conservative reservation, retained even if the encoded image is withheld.
    charged_pixels: int = 0


@dataclass(frozen=True)
class PinnedPdfSource:
    """Owned native handle, valid only inside pin_pdf_source's read-only context."""

    document: pymupdf.Document
    source_sha256: str
    source_bytes: bytes | None
    source_path: Path | None = None


@contextmanager
def pin_pdf_source(data: bytes, expected_sha256: str, settings: Settings):
    pinned = bytes(data)
    if len(pinned) > settings.source_reader_max_mb * 1024 * 1024:
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


@contextmanager
def pin_pdf_source_file(snapshot, settings: Settings):
    # The unique private copy stays alive until both native documents close.
    with snapshot.path.open("rb") as reader:
        digest = hashlib.file_digest(reader, "sha256").hexdigest()
    if digest != snapshot.sha256:
        raise ConflictError("The manuscript source no longer matches this evidence record.")
    with open_source_pdf(snapshot.path) as doc:
        if doc.page_count > settings.ingest_max_pages:
            raise ConflictError("The source exceeds the evidence-reading page limit.")
        yield PinnedPdfSource(doc, digest, None, snapshot.path)


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
        with (
            open_source_pdf(source.source_path)
            if source.source_path is not None
            else open_source_pdf_bytes(source.source_bytes)
        ) as extraction:
            return _locate_quote(extraction[region.page - 1], quote, coarse, settings)
    except (ConflictError, RuntimeError, ValueError, TypeError, AttributeError):
        return EvidencePlacementOut(**coarse, reason="quote_geometry_unverified")


def _textbox_reader(page):
    """`page.get_textbox(rect)` for many rectangles on one page.

    PyMuPDF's get_textbox builds a text page and checks every character of
    the page in Python on each call, which was most of a report export's
    work. This reads the same text page once, keeps each character's box,
    and applies the same rules per rectangle: a character counts when its
    box overlaps the rectangle, and lines that contributed text are joined
    with line breaks. Like get_textbox it compares in single precision and
    reports an unpaired surrogate as U+FFFD.
    """
    textpage = page.get_textpage()
    lines = [
        [
            ("�" if "\ud800" <= char["c"] <= "\udfff" else char["c"], char["bbox"])
            for span in line["spans"]
            for char in span["chars"]
        ]
        for block in textpage.extractRAWDICT()["blocks"]
        if block["type"] == 0
        for line in block["lines"]
    ]
    kept = sum(char != "\n" for chars in lines for char, _ in chars)
    if kept != sum(char != "\n" for char in textpage.extractText()):
        # The character listing leaves out some glyphs the text page holds
        # (zero-width boxes in some right-to-left scripts). Read such a page
        # the slow, exact way.
        return lambda rect: page.get_textbox(rect, textpage=textpage)

    def textbox(rect) -> str:
        x0, y0, x1, y1 = struct.unpack("4f", struct.pack("4f", *rect))
        parts = []
        pending_break = False
        for chars in lines:
            found = False
            for char, (cx0, cy0, cx1, cy1) in chars:
                if x0 >= cx1 or y0 >= cy1 or x1 <= cx0 or y1 <= cy0:
                    continue
                if pending_break:
                    parts.append("\n")
                    pending_break = False
                parts.append(char)
                found = True
            if found:
                pending_break = True
        return "".join(parts)

    return textbox


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
        textbox = _textbox_reader(page)
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
            portions.append(textbox(native))
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
    print_content_width: float | None = None,
    placement: EvidencePlacementOut | None = None,
) -> ReferencePageImage:
    """Reuse the native rasterizer with this reference's own admitted marks only.

    No cache is consulted. In particular private annotated images cannot satisfy
    public requests; the public page policy is inspected on this pinned document.
    A caller that already located this reference on this same pinned source
    passes that `placement`; locating is the costly half of the work.
    """
    if placement is None or placement.reference_id != reference.reference_id:
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
    rendered_pixels = 0
    try:
        render_settings = settings.model_copy(
            update={
                "export_page_image_max_pixels": min(
                    settings.export_page_image_max_pixels,
                    settings.evidence_source_max_rendered_pixels,
                )
            }
        )
        rendered_pixels = planned_page_image_pixels(
            source.document[placement.page - 1],
            llm_disclosure=disclosure,
            settings=render_settings,
            print_content_width=print_content_width,
        )
        image = _render_page_png(
            source.document[placement.page - 1],
            placement.boxes if placement.state == "text_layer_match" else (),
            llm_disclosure=disclosure,
            settings=render_settings,
            box_coordinates="pdf_points",
            print_content_width=print_content_width,
        )
    except PageImageUnavailable:
        return ReferencePageImage(
            EvidencePlacementOut(
                reference_id=reference.reference_id, state="unavailable", reason="resource_limit"
            ),
            None,
            rendered_pixels,
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
            max(rendered_pixels, width * height),
        )
    return ReferencePageImage(placement, image, max(rendered_pixels, width * height))
