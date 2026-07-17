"""PDF extraction via PyMuPDF (F1.1): page-anchored text, image inventory,
page geometry, section tree.

CPU-bound and synchronous on purpose — callers run it in a threadpool
(CODING.md §2 async discipline); nothing here touches the DB or the network.
"""

import pymupdf

from app.config import Settings
from app.errors import FileMalformedError
from app.ingest import sections
from app.ingest.normalize import furniture_key, match_key, normalize
from app.ingest.patterns import HeadingPatterns, load_patterns
from app.ingest.schemas import (
    Bbox,
    ExtractionResult,
    ImageBlock,
    Line,
    PageGeometry,
    TextBlock,
)
from app.messages import FILE_ENCRYPTED, FILE_UNREADABLE

_BOLD_FLAG = 16  # PyMuPDF span flag bit for bold fonts


def extract_document(path: str, settings: Settings) -> ExtractionResult:
    patterns = load_patterns(settings.ingest_patterns_file)
    try:
        doc = pymupdf.open(path)
    except (pymupdf.FileDataError, pymupdf.FileNotFoundError, RuntimeError, ValueError) as exc:
        raise FileMalformedError(FILE_UNREADABLE) from exc
    try:
        if doc.needs_pass:
            raise FileMalformedError(FILE_ENCRYPTED)
        return _extract_open_document(doc, patterns, settings)
    finally:
        doc.close()


def _extract_open_document(
    doc: pymupdf.Document, patterns: HeadingPatterns, settings: Settings
) -> ExtractionResult:
    lines_by_page: dict[int, list[Line]] = {}
    images: list[ImageBlock] = []
    geometry: list[PageGeometry] = []
    page_heights: dict[int, float] = {}

    for index in range(doc.page_count):
        page = doc[index]
        pno = index + 1  # 1-based everywhere outside PyMuPDF
        page_lines: list[Line] = []
        for block_no, block in enumerate(page.get_text("dict", sort=True)["blocks"]):
            bbox: Bbox = tuple(block["bbox"])
            if block["type"] == 1:
                images.append(ImageBlock(page=pno, bbox=bbox))
                continue
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s["text"].strip()]
                if not spans:
                    continue
                text = normalize("".join(s["text"] for s in line["spans"]))
                if not text:
                    continue
                chars = sum(len(s["text"]) for s in spans)
                bold_chars = sum(len(s["text"]) for s in spans if s["flags"] & _BOLD_FLAG)
                page_lines.append(
                    Line(
                        page=pno,
                        block_no=block_no,
                        bbox=tuple(line["bbox"]),
                        text=text,
                        max_size=max(s["size"] for s in spans),
                        bold_ratio=bold_chars / chars,
                    )
                )
        lines_by_page[pno] = page_lines
        page_heights[pno] = page.rect.height
        geometry.append(_page_geometry(page, pno))

    _mark_furniture(lines_by_page, page_heights, patterns, settings)

    blocks = _assemble_blocks(lines_by_page)
    text_chars = sum(len(b.text) for b in blocks if not b.is_furniture)
    page_count = doc.page_count
    image_only = (
        page_count > 0 and text_chars / page_count < settings.ingest_image_only_chars_per_page
    )

    page_keys = {
        pno: match_key(" ".join(ln.text for ln in lines)) for pno, lines in lines_by_page.items()
    }
    tree = sections.build_section_tree(lines_by_page, doc.get_toc(), page_keys, patterns, settings)

    return ExtractionResult(
        page_count=page_count,
        image_only=image_only,
        text_chars=text_chars,
        section_tree=tree,
        blocks=blocks,
        images=images,
        geometry=geometry,
    )


def _page_geometry(page: pymupdf.Page, pno: int) -> PageGeometry:
    words = page.get_text("words")
    text_bbox: Bbox | None = None
    margins: tuple[float, float, float, float] | None = None
    if words:
        text_bbox = (
            min(w[0] for w in words),
            min(w[1] for w in words),
            max(w[2] for w in words),
            max(w[3] for w in words),
        )
        rect = page.rect
        margins = (
            text_bbox[0] - rect.x0,
            text_bbox[1] - rect.y0,
            rect.x1 - text_bbox[2],
            rect.y1 - text_bbox[3],
        )
    return PageGeometry(
        page=pno,
        width=page.rect.width,
        height=page.rect.height,
        rotation=page.rotation,
        text_bbox=text_bbox,
        margins=margins,
    )


def _mark_furniture(
    lines_by_page: dict[int, list[Line]],
    page_heights: dict[int, float],
    patterns: HeadingPatterns,
    settings: Settings,
) -> None:
    """Flag running headers/footers: same text (digits masked, so varying
    page numbers inside a header still group) recurring in the top/bottom
    band across enough pages — plus bare page-number lines (data-file
    patterns) regardless of repetition.

    Exemption: a heading-shaped line whose exact text occurs on only one
    page is never repetition-furniture — chapters routinely start at the
    very top of a page, and in a many-chapter document their masked keys
    ("chapter #") collide exactly like a running header's."""
    page_count = len(lines_by_page)

    def band(ln: Line) -> str | None:
        height = page_heights[ln.page]
        if ln.bbox[3] <= height * settings.ingest_furniture_band_ratio:
            return "top"
        if ln.bbox[1] >= height * (1 - settings.ingest_furniture_band_ratio):
            return "bottom"
        return None

    masked_pages: dict[tuple[str, str], set[int]] = {}
    exact_pages: dict[tuple[str, str], set[int]] = {}
    for lines in lines_by_page.values():
        for ln in lines:
            b = band(ln)
            if b is not None:
                masked_pages.setdefault((b, furniture_key(ln.text)), set()).add(ln.page)
                exact_pages.setdefault((b, match_key(ln.text)), set()).add(ln.page)

    repeating = {
        key
        for key, pages in masked_pages.items()
        if page_count >= settings.ingest_furniture_min_pages
        and len(pages) / page_count >= settings.ingest_furniture_page_ratio
    }

    for lines in lines_by_page.values():
        for ln in lines:
            b = band(ln)
            if b is None:
                continue
            if any(p.match(ln.text) for p in patterns.furniture):
                ln.is_furniture = True
                continue
            if (b, furniture_key(ln.text)) in repeating:
                unique = len(exact_pages[(b, match_key(ln.text))]) == 1
                if unique and sections.heading_shaped(ln.text, patterns):
                    continue
                ln.is_furniture = True


def _assemble_blocks(lines_by_page: dict[int, list[Line]]) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    for pno in sorted(lines_by_page):
        by_block: dict[int, list[Line]] = {}
        for ln in lines_by_page[pno]:
            by_block.setdefault(ln.block_no, []).append(ln)
        for block_no in sorted(by_block):
            lines = by_block[block_no]
            chars = sum(len(ln.text) for ln in lines)
            blocks.append(
                TextBlock(
                    page=pno,
                    bbox=(
                        min(ln.bbox[0] for ln in lines),
                        min(ln.bbox[1] for ln in lines),
                        max(ln.bbox[2] for ln in lines),
                        max(ln.bbox[3] for ln in lines),
                    ),
                    text="\n".join(ln.text for ln in lines),
                    max_font_size=max(ln.max_size for ln in lines),
                    bold_ratio=(
                        sum(ln.bold_ratio * len(ln.text) for ln in lines) / chars if chars else 0.0
                    ),
                    is_furniture=all(ln.is_furniture for ln in lines),
                )
            )
    return blocks
