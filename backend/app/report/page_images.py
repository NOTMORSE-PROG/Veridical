"""V-070: renders each page-anchored flag's ACTUAL SUBMITTED PAGE as a
raster image for the PDF export, with the flagged region marked whenever a
bounding box was recoverable -- the owner's own framing: "screenshots of
like faults on the submitted pdf, literally pages not just created."

Shares `app.ingest.regions.recover_region` with V-065's manuscript viewer
(both tickets' own Q5 -- whichever ships first extracts it for the other;
this reuses V-065's `AnchorRegion`, not a second anchor-classification
mechanism). Degrades honestly per `AnchorRegion.kind`, same discipline as
`regions.py` itself: never fabricates a box, a page, or an image for a
source this can't be produced from (AC3 -- purged source, DOCX source, and
an unrecoverable anchor each get an explicit stated reason).

Coordinate note: `recover_region`'s boxes are in bottom-left-origin "PDF
point space" (`regions.py`'s own `_search_excerpt` docstring: the space
pdf.js's `convertToViewportPoint` expects). PyMuPDF's own drawing/
annotation API (`Page.add_rect_annot`) expects its NATIVE top-left-origin
convention instead -- the same one `search_for()` returns before
`_search_excerpt` flips it. `_to_mupdf_rect` below undoes that flip.
"""

from dataclasses import dataclass
from math import ceil
from typing import Literal

import pymupdf
from reportlab.lib.pagesizes import A4

from app.config import Settings
from app.ingest.regions import AnchorRegion, recover_region
from app.ingest.schemas import SectionTree
from app.report.export import _LLM_MODE_DISCLOSURE, _PRINT_LAYOUT, _TONE_COLORS
from app.report.page_image_disclosure import (
    PageImageUnavailable,
    append_disclosure,
    check_pixel_budget,
    disclosure_layout,
)
from app.report.schemas import FlagSummaryOut, PageEvidenceImageOut

Bbox = tuple[float, float, float, float]

_UNAVAILABLE_REASON = "The submitted page for this flag could not be located."


@dataclass(frozen=True)
class _RegionRender:
    page: int | None
    boxes: tuple[Bbox, ...]
    note: str | None
    # Which `AnchorRegion.kind` produced this -- `section`/`whole_document`
    # both resolve to `boxes=()` like several other kinds, but only these
    # two need the originality/reuse "other manuscript not shown" note
    # appended by the caller (it doesn't apply to e.g. `reference_list`).
    kind: str


def _to_mupdf_rect(bbox: Bbox, page_height: float) -> pymupdf.Rect:
    x0, y0, x1, y1 = bbox
    return pymupdf.Rect(x0, page_height - y1, x1, page_height - y0)


def _merge_line_boxes(boxes: tuple[Bbox, ...], tolerance: float) -> list[Bbox]:
    """`page.search_for()` returns one rect PER WORD-RUN on a matched line,
    not one rect for the whole phrase (measured live, 2026-08-25, against a
    real quoted excerpt on the owner's real manuscript) -- drawing each
    separately is technically correct but reads as a broken/partial mark.
    Groups boxes whose `y0` (bottom-left-origin space, so equal y0 really
    does mean "same line") sit within `tolerance` of each other and unions
    each group into one rect spanning it."""
    groups: list[list[Bbox]] = []
    for box in sorted(boxes, key=lambda b: (-b[1], b[0])):
        placed = False
        for group in groups:
            if abs(group[-1][1] - box[1]) <= tolerance:
                group.append(box)
                placed = True
                break
        if not placed:
            groups.append([box])
    merged = []
    for group in groups:
        x0 = min(b[0] for b in group)
        y0 = min(b[1] for b in group)
        x1 = max(b[2] for b in group)
        y1 = max(b[3] for b in group)
        merged.append((x0, y0, x1, y1))
    return merged


def _region_render(region: AnchorRegion) -> _RegionRender | None:
    """Classifies one already-recovered `AnchorRegion` into what this
    module can do with it: a page number, zero or more boxes to draw, and
    an honest caption note for anything less than a precise mark. Returns
    None for kinds this module cannot turn into a page image at all
    (`paragraph_only`, `unavailable`) -- the caller states the reason."""
    if region.kind == "page_bbox":
        return _RegionRender(page=region.page, boxes=region.all_bboxes, note=None, kind=region.kind)
    if region.kind == "page_only":
        return _RegionRender(
            page=region.page,
            boxes=(),
            note=(
                "Page shown; the exact passage could not be located automatically "
                "for a precise mark."
            ),
            kind=region.kind,
        )
    if region.kind in ("reference_list", "reference_position"):
        return _RegionRender(
            page=region.page,
            boxes=(),
            note="Page shown; the exact reference entry could not be boxed automatically.",
            kind=region.kind,
        )
    if region.kind == "section":
        span = (
            f" (through page {region.end_page})"
            if region.end_page and region.end_page != region.page
            else ""
        )
        return _RegionRender(
            page=region.page,
            boxes=(),
            note=(
                f"This flag concerns the chapter beginning on page {region.page}{span}; "
                "showing the first page only."
            ),
            kind=region.kind,
        )
    if region.kind == "whole_document":
        note = (
            f"This flag concerns the entire {region.end_page}-page document; showing page 1 only."
            if region.end_page
            else "This flag concerns the entire document; showing page 1 only."
        )
        return _RegionRender(page=region.page, boxes=(), note=note, kind=region.kind)
    return None


def _render_page_png(
    page: pymupdf.Page,
    boxes: tuple[Bbox, ...],
    *,
    llm_disclosure: tuple[str, str] | None,
    settings: Settings,
) -> bytes:
    """Render the visible source unchanged, with an optional external notice.

    Only finding marks shared by all consumers are added. The original page
    is rasterized with its own layer/annotation state, then marks are drawn
    directly on those pixels. No page copy can lose document-level visibility.
    """
    raster_rect = (
        page.rect
        * pymupdf.Matrix(settings.export_page_image_dpi / 72, settings.export_page_image_dpi / 72)
    ).irect
    check_pixel_budget(raster_rect.width, raster_rect.height, settings)
    source = page.get_pixmap(
        dpi=settings.export_page_image_dpi, colorspace=pymupdf.csRGB, alpha=False, annots=True
    )
    check_pixel_budget(source.width, source.height, settings)
    if boxes:
        merged = _merge_line_boxes(boxes, settings.export_box_line_merge_tolerance)
        box_color = _TONE_COLORS["danger"][1]
        color = tuple(round(c * 255) for c in (box_color.red, box_color.green, box_color.blue))
        dpi_scale = settings.export_page_image_dpi / 72
        stroke = ceil(disclosure_layout()["highlight_stroke_pt"] * dpi_scale)
        transform = page.rotation_matrix * pymupdf.Matrix(dpi_scale, dpi_scale)
        for bbox in merged:
            # Recovered coordinates are unrotated. Map into the same visible
            # viewport as get_pixmap, then clip each border through set_rect.
            rect = _to_mupdf_rect(bbox, page.rect.height) * transform
            border = (rect + (-stroke / 2, -stroke / 2, stroke / 2, stroke / 2)).irect
            for edge in (
                (border.x0, border.y0, border.x1, border.y0 + stroke),
                (border.x0, border.y1 - stroke, border.x1, border.y1),
                (border.x0, border.y0, border.x0 + stroke, border.y1),
                (border.x1 - stroke, border.y0, border.x1, border.y1),
            ):
                source.set_rect(edge, color)
    if llm_disclosure is None:
        return source.tobytes("png")
    text, tone = llm_disclosure
    fg, bg = _TONE_COLORS[tone][1], _TONE_COLORS[tone][0]
    return append_disclosure(
        source,
        text,
        (fg.red, fg.green, fg.blue),
        (bg.red, bg.green, bg.blue),
        content_width=A4[0] - 2 * _PRINT_LAYOUT["side_margin"],
        settings=settings,
    )


_REUSE_OTHER_SIDE_NOTE = (
    "Only this manuscript's page is shown. The other manuscript in this "
    "comparison is not included in this export."
)

_SHARED_PAGE_NOTE = (
    "Several findings use this source page with different locations. "
    "This shared page image has no finding-specific marks; compare the excerpt "
    "with the source or open the on-screen evidence view."
)


def build_page_evidence_images(
    doc: pymupdf.Document | None,
    section_tree: SectionTree,
    flags: list[FlagSummaryOut],
    *,
    source_format: Literal["pdf", "docx", "unknown"],
    unavailable_reason: str | None,
    llm_mode: str,
    settings: Settings,
) -> dict[int, PageEvidenceImageOut]:
    """The export-side counterpart to `app.report.service.manuscript_viewer_for`
    -- same file already opened by the caller (`doc`/`source_format`/
    `unavailable_reason`, from `_open_manuscript_source`), turned into
    actual page images instead of viewer regions. `unavailable_reason` is
    the purge/open-failure reason the caller already computed; it is used
    verbatim here rather than re-derived, so the export and the on-screen
    viewer never state the reason two different ways for the same
    manuscript (the exact drift class BUG-082's history warns about).

    Each distinct source page is rendered once, within export_max_page_images.
    All findings using that page receive the same image. Marks are retained
    only if every consumer has the same box set; otherwise the page is shown
    without marks and each finding gets an explicit shared-page disclosure.
    An empty box set participates in this comparison, so page-only or whole-
    document findings never inherit another finding's precise rectangles.
    """
    if source_format == "docx" and unavailable_reason is None:
        reason = (
            "Page images are available for PDF submissions only; this manuscript "
            "was submitted as a DOCX."
        )
        return {
            f.id: PageEvidenceImageOut(
                image_png=None, page=None, reason=reason, reason_code="docx_layout"
            )
            for f in flags
        }
    if doc is None:
        reason = unavailable_reason or _UNAVAILABLE_REASON
        return {f.id: PageEvidenceImageOut(image_png=None, page=None, reason=reason) for f in flags}

    llm_disclosure = _LLM_MODE_DISCLOSURE.get(llm_mode)
    disclosure = (llm_disclosure[0], llm_disclosure[1]) if llm_disclosure is not None else None

    # Pass 1: classify every flag's region once.
    renders: dict[int, _RegionRender | None] = {}
    for flag in flags:
        region = recover_region(
            doc, section_tree, flag.page_anchor, flag.evidence_excerpt, settings=settings
        )
        renders[flag.id] = _region_render(region)

    # Pass 2: preserve finding-specific meaning within a page-keyed cache.
    boxes_by_page: dict[int, tuple[Bbox, ...]] = {}
    differing_locations: set[int] = set()
    for rendered in renders.values():
        if rendered is None or rendered.page is None:
            continue
        if not (1 <= rendered.page <= doc.page_count):
            continue
        boxes = tuple(sorted(set(rendered.boxes)))
        if rendered.page not in boxes_by_page:
            boxes_by_page[rendered.page] = boxes
        elif boxes_by_page[rendered.page] != boxes:
            differing_locations.add(rendered.page)

    # Pass 3: render each distinct page once, capped by real page count.
    page_cache: dict[int, bytes | None] = {}
    unavailable_pages: dict[int, str] = {}
    for page_no, boxes in boxes_by_page.items():
        if len(page_cache) >= settings.export_max_page_images:
            continue
        # An attempted distinct page consumes its budget even if no safe image
        # can be produced. Later findings reuse that same explicit reason.
        page_cache[page_no] = None
        try:
            page_cache[page_no] = _render_page_png(
                doc[page_no - 1],
                () if page_no in differing_locations else boxes,
                llm_disclosure=disclosure,
                settings=settings,
            )
        except PageImageUnavailable as exc:
            unavailable_pages[page_no] = str(exc)

    # Pass 4: assemble each flag's own result from the shared page cache.
    results: dict[int, PageEvidenceImageOut] = {}
    for flag in flags:
        rendered = renders[flag.id]
        if rendered is None or rendered.page is None or not (1 <= rendered.page <= doc.page_count):
            results[flag.id] = PageEvidenceImageOut(
                image_png=None, page=None, reason=_UNAVAILABLE_REASON
            )
            continue

        image = page_cache.get(rendered.page)
        if image is None:
            results[flag.id] = PageEvidenceImageOut(
                image_png=None,
                page=rendered.page,
                reason=unavailable_pages.get(rendered.page)
                or (
                    "Page image skipped: this export's rendered-page limit "
                    f"({settings.export_max_page_images}) was reached."
                ),
            )
            continue

        note = rendered.note
        if rendered.page in differing_locations:
            note = f"{note} {_SHARED_PAGE_NOTE}" if note else _SHARED_PAGE_NOTE
        # design review finding, 2026-08-25: a reuse flag anchored to a
        # chapter/whole-document span was disclosing WHICH page is shown
        # but not that the MATCHED manuscript (the other half of the
        # comparison this flag is actually about) isn't shown at all --
        # the ticket's own named edge case, not resolved by the span note
        # alone. Only applies to `originality_reuse` flags: a
        # section/whole_document region can in principle occur for other
        # check kinds, and this note would be a non-sequitur there.
        if flag.check_kind == "originality_reuse" and rendered.kind in (
            "section",
            "whole_document",
        ):
            note = f"{note} {_REUSE_OTHER_SIDE_NOTE}" if note else _REUSE_OTHER_SIDE_NOTE
        results[flag.id] = PageEvidenceImageOut(image_png=image, page=rendered.page, reason=note)

    return results
