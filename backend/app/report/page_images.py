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
from typing import Literal

import pymupdf

from app.config import Settings
from app.ingest.regions import AnchorRegion, recover_region
from app.ingest.schemas import SectionTree
from app.report.export import _LLM_MODE_DISCLOSURE, _TONE_COLORS
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


_DISCLOSURE_FONT = "hebo"


def _wrapped_line_count(text: str, fontname: str, fontsize: float, max_width: float) -> int:
    """Greedy word-wrap simulation to ESTIMATE how many lines `text` needs
    at `fontsize` within `max_width` -- `pymupdf.insert_textbox` does the
    real wrapping itself and is the actual authority (`_draw_disclosure_band`
    verifies against its real return value), this is only a starting guess
    so the common case fits on the first attempt instead of the retry loop."""
    words = text.split()
    if not words:
        return 1
    space_width = pymupdf.get_text_length(" ", fontname=fontname, fontsize=fontsize)
    lines = 1
    current_width = 0.0
    for word in words:
        word_width = pymupdf.get_text_length(word, fontname=fontname, fontsize=fontsize)
        if current_width and current_width + space_width + word_width > max_width:
            lines += 1
            current_width = word_width
        else:
            current_width += (space_width if current_width else 0.0) + word_width
    return lines


def _draw_disclosure_band(
    page: pymupdf.Page,
    text: str,
    fontsize: float,
    fg_rgb: tuple[float, float, float],
    bg_rgb: tuple[float, float, float],
) -> None:
    """Draws the fake/unknown-`llm_mode` disclosure directly onto the page
    before rasterizing (AC5). `insert_textbox` does NOT raise or clip when
    the text overflows its box -- it silently draws NOTHING and returns a
    NEGATIVE number (the overflow amount) instead (`professor` finding,
    2026-08-25, reproduced live: the disclosure string for `llm_mode=
    'unknown'` silently failed to render at all against a real A4 page,
    while the band's background alone still painted -- a visually "fine"
    but textless banner, the exact failure this AC exists to prevent).
    Estimates a starting band height from real font-metric wrapping, then
    VERIFIES against `insert_textbox`'s own return value and grows the
    band if the estimate undershot, rather than trusting the estimate."""
    page_width = page.rect.width
    page_height = page.rect.height
    inset = fontsize * 0.6
    n_lines = _wrapped_line_count(text, _DISCLOSURE_FONT, fontsize, page_width - 2 * inset)
    height = n_lines * fontsize * 1.3 + inset * 2

    for _ in range(6):  # bounded safety net; the estimate above should land on attempt 1
        band = pymupdf.Rect(0, page_height - height, page_width, page_height)
        page.draw_rect(band, color=None, fill=bg_rgb, fill_opacity=0.92, overlay=True)
        fit = page.insert_textbox(
            band, text, fontsize=fontsize, fontname=_DISCLOSURE_FONT, color=fg_rgb, align=1
        )
        if fit >= 0:
            return
        height *= 1.4


def _render_page_png(
    page: pymupdf.Page,
    boxes: tuple[Bbox, ...],
    *,
    llm_disclosure: tuple[str, str] | None,
    settings: Settings,
) -> bytes:
    """Renders ONE page, with every box in `boxes` marked -- `boxes` is the
    UNION of every flag's own boxes that resolved to this page number
    (`build_page_evidence_images` collects that union before calling this),
    so a page carrying several distinct flags gets exactly one real
    `get_pixmap` call, never one per flag (`backend-critic` finding,
    2026-08-25: the previous per-(page, boxes) cache key rendered the SAME
    page again for every distinct excerpt on it, defeating both the
    ticket's own dedup requirement and `export_max_page_images`'s memory
    argument, which prices the cap in DISTINCT PAGES)."""
    page_height = page.rect.height
    annots = []
    merged = _merge_line_boxes(boxes, settings.export_box_line_merge_tolerance) if boxes else []
    box_color = _TONE_COLORS["danger"][1]
    box_rgb = (box_color.red, box_color.green, box_color.blue)
    for bbox in merged:
        rect = _to_mupdf_rect(bbox, page_height)
        annot = page.add_rect_annot(rect)
        annot.set_colors(stroke=box_rgb)
        annot.set_border(width=1.4)
        annot.update()
        annots.append(annot)

    # AC5: a fake/unknown-mode run's page images must carry the SAME
    # disclosure the header/footer already state (BUG-049/BUG-113) --
    # baked into the raster itself (not just an adjacent caption) so it
    # can't be separated from the image across a page break. Reuses
    # export.py's own single-sourced wording/tone, never a re-derived copy
    # -- including the BACKGROUND fill, not only the text color
    # (`ux-critic`-class finding, 2026-08-25: the fill used to be a
    # hand-typed constant that ignored `tone`, so 'unknown' (caution) read
    # visually as 'fake' (danger) on the page itself).
    if llm_disclosure is not None:
        text, tone = llm_disclosure
        fg = _TONE_COLORS[tone][1]
        bg = _TONE_COLORS[tone][0]
        fg_rgb = (fg.red, fg.green, fg.blue)
        bg_rgb = (bg.red, bg.green, bg.blue)
        # `page_images.py` embeds this raster at up to `export_page_image_max_height`
        # points tall regardless of the source page's own (often much
        # taller) native height -- a fixed native fontsize would shrink
        # proportionally on a tall page and become illegible at the
        # embedded size (`ui-designer` finding, 2026-08-25: measured ~4.8pt
        # effective on a real Letter-height page). Scaling the native
        # fontsize by how much this page will later be shrunk keeps the
        # EMBEDDED size at roughly `export_page_disclosure_font_pt`
        # regardless of source page height.
        shrink = min(1.0, settings.export_page_image_max_height / page_height)
        native_fontsize = settings.export_page_disclosure_font_pt / shrink
        _draw_disclosure_band(page, text, native_fontsize, fg_rgb, bg_rgb)

    try:
        pix = page.get_pixmap(dpi=settings.export_page_image_dpi, annots=True)
        return pix.tobytes("png")
    finally:
        for annot in annots:
            page.delete_annot(annot)


_REUSE_OTHER_SIDE_NOTE = (
    "Only this manuscript's page is shown. The other manuscript in this "
    "comparison is not included in this export."
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

    Two passes on purpose (`backend-critic` finding, 2026-08-25): pass 1
    classifies every flag's region and collects the UNION of boxes per
    PAGE NUMBER (several flags routinely share a page); pass 2 renders
    each DISTINCT page exactly once, bounded by `export_max_page_images`
    counting real pages -- not once per flag, and not once per distinct
    excerpt. Every flag whose region resolves to that page gets the SAME
    shared image (with every box that belongs on that page marked, not
    only its own) -- still real, honest marks; a reader matches a flag's
    own excerpt text to its box the same way they would reading the raw
    page directly."""
    if source_format == "docx":
        reason = (
            "Page images are available for PDF submissions only; this manuscript "
            "was submitted as a DOCX."
        )
        return {f.id: PageEvidenceImageOut(image_png=None, page=None, reason=reason) for f in flags}
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

    # Pass 2: the union of boxes per page, across every flag anchored there.
    boxes_by_page: dict[int, list[Bbox]] = {}
    for rendered in renders.values():
        if rendered is None or rendered.page is None:
            continue
        if not (1 <= rendered.page <= doc.page_count):
            continue
        boxes_by_page.setdefault(rendered.page, []).extend(rendered.boxes)

    # Pass 3: render each distinct page once, capped by real page count.
    page_cache: dict[int, bytes] = {}
    for page_no, boxes in boxes_by_page.items():
        if len(page_cache) >= settings.export_max_page_images:
            continue
        page_cache[page_no] = _render_page_png(
            doc[page_no - 1], tuple(boxes), llm_disclosure=disclosure, settings=settings
        )

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
                reason=(
                    "Page image skipped: this export's rendered-page limit "
                    f"({settings.export_max_page_images}) was reached."
                ),
            )
            continue

        note = rendered.note
        # `ui-designer` finding, 2026-08-25: a reuse flag anchored to a
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
