"""PDF extraction via PyMuPDF (F1.1): page-anchored text, image inventory,
page geometry, section tree.

CPU-bound and synchronous on purpose — callers run it in a threadpool
(CODING.md §2 async discipline); nothing here touches the DB or the network.
"""

import math
import threading
from itertools import pairwise

import pymupdf
from pydantic import ValidationError

from app.config import Settings
from app.errors import FileMalformedError, FileTooLargeError
from app.ingest import sections
from app.ingest.native_cache import release_native_cache
from app.ingest.native_table_order import (
    NativeTableProposal,
    admit_native_tables,
    capture_native_characters,
    snapshot_native_table,
)
from app.ingest.normalize import furniture_key, match_key, normalize
from app.ingest.patterns import HeadingPatterns, load_patterns
from app.ingest.reading_order import line_source_id, order_page, propose_regions
from app.ingest.schemas import (
    Bbox,
    ExtractionResult,
    ImageBlock,
    Line,
    PageGeometry,
    TableBlock,
    TextBlock,
    UriLink,
)
from app.messages import FILE_ENCRYPTED, FILE_UNREADABLE, PDF_TOO_MANY_PAGES

_BOLD_FLAG = 16  # PyMuPDF span flag bit for bold fonts
_NATIVE_TABLE_LOCK = threading.Lock()


def _open_document(path: str) -> pymupdf.Document:
    """Translate a failed native open after releasing its exception context.

    A failed filename constructor can retain a file stream in its traceback.
    Chaining that traceback into the upload error keeps the source open while
    the caller tries to remove the rejected file, which Windows refuses.
    """
    try:
        return pymupdf.open(path)
    except (pymupdf.FileDataError, pymupdf.FileNotFoundError, RuntimeError, ValueError):
        pass
    raise FileMalformedError(FILE_UNREADABLE)


def extract_document(path: str, settings: Settings) -> ExtractionResult:
    patterns = load_patterns(settings.ingest_patterns_file)
    doc = _open_document(path)
    try:
        if doc.needs_pass:
            raise FileMalformedError(FILE_ENCRYPTED)
        # BUG-180: checked before a single page is parsed -- `max_upload_mb`
        # bounds compressed file size, the wrong dimension for a PDF (a
        # 50,000-page, 15.9MB file measured well inside that cap, 6.1s
        # parse, 1.29M chars). Page count is what actually drives the
        # unbounded `for index in range(doc.page_count)` loop below.
        if doc.page_count > settings.ingest_max_pages:
            raise FileTooLargeError(
                PDF_TOO_MANY_PAGES.format(
                    page_count=doc.page_count, limit=settings.ingest_max_pages
                )
            )
        return _extract_open_document(doc, patterns, settings)
    finally:
        doc.close()


def _extract_open_document(
    doc: pymupdf.Document, patterns: HeadingPatterns, settings: Settings
) -> ExtractionResult:
    lines_by_page: dict[int, list[Line]] = {}
    images: list[ImageBlock] = []
    tables: list[TableBlock] = []
    geometry: list[PageGeometry] = []
    page_heights: dict[int, float] = {}
    uri_links: list[UriLink] = []
    uri_links_remaining = settings.ingest_uri_max_links
    uri_links_complete = True
    ordering_enabled = settings.ingest_reading_order_strategy == "native_regions_v1"
    order_proposals = {}

    for index in range(doc.page_count):
        page = doc[index]
        pno = index + 1  # 1-based everywhere outside PyMuPDF
        # Admit a whole page's links from the smaller xref inventory before
        # materializing URI dictionaries/clips. Count internal links too; once
        # exhausted, do not enumerate annotations on any subsequent page.
        # Native PDF parsing/xref inventory is still bounded by upload/page
        # limits, not by this downstream link-processing budget.
        page_links = []
        if uri_links_remaining <= 0:
            uri_links_complete = False
        else:
            link_count = sum(kind == pymupdf.PDF_ANNOT_LINK for _, kind, _ in page.annot_xrefs())
            if link_count > uri_links_remaining:
                uri_links_remaining = 0
                uri_links_complete = False
            else:
                uri_links_remaining -= link_count
                page_links = page.get_links() if link_count else []
                if len(page_links) > link_count:
                    page_links = []
                    uri_links_remaining = 0
                    uri_links_complete = False
        # Destinations are untrusted source strings, not verified sources.
        # Reuse one text page instead of re-extracting it for every rectangle.
        link_textpage = None
        for link in page_links:
            if link.get("kind") != pymupdf.LINK_URI:
                continue
            uri = link.get("uri")
            rect = link.get("from")
            # get_links() hotspots follow page rotation in the installed PDF
            # reader; text and block geometry use unrotated crop-relative space.
            rect = rect * page.derotation_matrix if rect is not None else None
            visible_page = page.rect * page.derotation_matrix
            if (
                not isinstance(uri, str)
                or len(uri) > settings.ingest_uri_max_chars
                or rect is None
                or rect.is_empty
                or rect.is_infinite
                or not visible_page.contains(rect)
            ):
                uri_links_complete = False
                continue
            if link_textpage is None:
                link_textpage = page.get_textpage(flags=pymupdf.TEXTFLAGS_TEXT)
            displayed = page.get_textbox(rect, textpage=link_textpage)
            if len(displayed) > settings.ingest_uri_max_chars:
                uri_links_complete = False
                continue
            try:
                uri_links.append(UriLink(page=pno, bbox=tuple(rect), uri=uri, text=displayed))
            except ValidationError:
                uri_links_complete = False
        # BUG-163: native table extraction existed for DOCX only --
        # `ExtractionResult.tables` stayed empty for every PDF, so
        # `forensics/extract.py`'s `extract_descriptive_stats(extraction.
        # tables)` had nothing to read on the format everyone actually
        # submits. Same flat `rows: list[list[str]]` shape DOCX already
        # produces (`ingest/docx.py`) -- no header/caption detection here
        # either, matching that precedent exactly rather than introducing
        # a format-specific difference downstream code doesn't expect.
        table_proposal = NativeTableProposal() if ordering_enabled else None
        tables.extend(
            _native_tables_in_source_space(page, pno, ordering=table_proposal, settings=settings)
        )
        page_lines: list[Line] = []
        raw_blocks = page.get_text("dict", sort=True)["blocks"]
        for block_no, block in enumerate(raw_blocks):
            bbox: Bbox = tuple(block["bbox"])
            if block["type"] == 1:
                images.append(ImageBlock(page=pno, bbox=bbox))
                continue
            for line_no, line in enumerate(block.get("lines", [])):
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
                        source_id=(
                            line_source_id(pno, block, block_no, line_no)
                            if ordering_enabled
                            else None
                        ),
                    )
                )
        if ordering_enabled:
            order_proposals[pno] = propose_regions(page, raw_blocks, page_lines, settings)
            if table_proposal and (table_proposal.grids or table_proposal.problem):
                capture_native_characters(page, raw_blocks, page_lines, table_proposal, settings)
                order_proposals[pno].table_proposal = table_proposal
        lines_by_page[pno] = page_lines
        page_heights[pno] = page.rect.height
        geometry.append(_page_geometry(page, pno))
        # Decoded page images stay in MuPDF's process-wide store until it is
        # emptied; holding every page's images pushed long theses past 512 MiB.
        release_native_cache()

    _mark_furniture(lines_by_page, page_heights, patterns, settings)

    reading_order = [] if ordering_enabled else None
    native_furniture = {}
    if ordering_enabled:
        table_pages = {table.page for table in tables}
        for pno, lines in lines_by_page.items():
            for line in lines:
                key = (pno, line.block_no)
                native_furniture[key] = native_furniture.get(key, True) and line.is_furniture
            lines_by_page[pno], order_record = order_page(
                pno, lines, order_proposals[pno], has_tables=pno in table_pages, settings=settings
            )
            reading_order.append(order_record)

    blocks = _assemble_blocks(
        lines_by_page, preserve_sequence=ordering_enabled, native_furniture=native_furniture
    )
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
        anchor_kind="page",
        image_only=image_only,
        text_chars=text_chars,
        section_tree=tree,
        blocks=blocks,
        images=images,
        tables=tables,
        geometry=geometry,
        uri_links=uri_links,
        uri_links_complete=uri_links_complete,
        reading_order=reading_order,
    )


def _native_tables_in_source_space(
    page: pymupdf.Page,
    pno: int,
    *,
    ordering: NativeTableProposal | None = None,
    settings: Settings | None = None,
) -> list[TableBlock]:
    # PyMuPDF Table.extract reads module-global character state populated by
    # find_tables. Keep discovery, materialization and restoration indivisible.
    with _NATIVE_TABLE_LOCK:
        return _native_tables_in_source_space_locked(
            page, pno, ordering=ordering, settings=settings
        )


def _native_tables_in_source_space_locked(
    page: pymupdf.Page,
    pno: int,
    *,
    ordering: NativeTableProposal | None = None,
    settings: Settings | None = None,
) -> list[TableBlock]:
    """Materialize native tables in unrotated, crop-relative source coordinates.

    Rotated table detection can reset the visible crop. Restore page state even
    on failure, before subsequent source extraction. Never rewrite the upload.
    """
    rotation, crop = page.rotation, pymupdf.Rect(page.cropbox)
    try:
        if rotation:
            page.set_rotation(0)
        native = page.find_tables().tables
        if ordering is not None:
            ordering.page_bbox = tuple(page.rect)
            admit_native_tables(native, ordering, settings)
        output = []
        for table in native:
            rows = table.extract()
            output.append(
                TableBlock(
                    page=pno,
                    bbox=tuple(table.bbox),
                    rows=[[normalize(cell or "") for cell in row] for row in rows],
                )
            )
            if ordering is not None:
                snapshot_native_table(table, rows, ordering, settings)
        return output
    finally:
        if page.cropbox != crop:
            page.set_cropbox(crop)
        if page.rotation != rotation:
            page.set_rotation(rotation)


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

    repeated_top: dict[int, list[Line]] = {}
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
                if b == "top":
                    repeated_top.setdefault(ln.page, []).append(ln)

    # Repetition in a wide margin band can also describe the first body line.
    # Restore only the immediate continuation of an already-retained unique
    # heading, supported by one disjoint source column continuing below the
    # band. The downstream order/overlap/table guards remain authoritative.
    eligible_top: dict[int, list[Line]] = {}
    for page, candidates in repeated_top.items():
        lines = lines_by_page[page]
        height = page_heights[page]
        if len(lines) > settings.ingest_reading_order_max_lines_per_page:
            continue
        if (
            not math.isfinite(height)
            or height <= 0
            or any(
                not all(math.isfinite(value) for value in line.bbox)
                or line.bbox[0] >= line.bbox[2]
                or line.bbox[1] >= line.bbox[3]
                for line in lines
            )
        ):
            continue
        eligible_top[page] = candidates
    if not eligible_top:
        return

    listing_pages = sections.toc_page_numbers(lines_by_page, patterns, settings)
    for page, candidates in eligible_top.items():
        if page in listing_pages:
            continue
        lines = lines_by_page[page]
        height = page_heights[page]
        ordered = sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0]))
        candidate_ids = {id(line) for line in candidates}
        body_candidates = [
            line
            for previous, line in pairwise(ordered)
            if id(line) in candidate_ids
            and not previous.is_furniture
            and band(previous) == "top"
            and len(exact_pages[("top", match_key(previous.text))]) == 1
            and sections.heading_shaped(previous.text, patterns)
            and previous.bbox[3] < line.bbox[1]
        ]
        if not body_candidates:
            continue
        restore_ids = {id(line) for line in body_candidates}
        body = [line for line in ordered if not line.is_furniture or id(line) in restore_ids]
        if max(line.bbox[0] for line in body) >= min(line.bbox[2] for line in body):
            continue
        if any(left.bbox[3] >= right.bbox[1] for left, right in pairwise(body)):
            continue
        if not any(
            not line.is_furniture
            and line.bbox[1] >= height * settings.ingest_furniture_band_ratio
            and not sections.heading_shaped(line.text, patterns)
            for line in body
        ):
            continue
        for line in body_candidates:
            line.is_furniture = False


def _assemble_blocks(
    lines_by_page: dict[int, list[Line]],
    *,
    preserve_sequence: bool = False,
    native_furniture: dict[tuple[int, int], bool] | None = None,
) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    for pno in sorted(lines_by_page):
        by_block: dict[int, list[Line]] = {}
        for ln in lines_by_page[pno]:
            by_block.setdefault(ln.block_no, []).append(ln)
        groups = []
        if preserve_sequence:
            # A native block can contain lines from both columns. Retain its
            # identity but never merge noncontiguous runs back across a gutter.
            for line in lines_by_page[pno]:
                if not groups or groups[-1][0].block_no != line.block_no:
                    groups.append([line])
                else:
                    groups[-1].append(line)
        else:
            groups = [by_block[block_no] for block_no in sorted(by_block)]
        for lines in groups:
            chars = sum(len(ln.text) for ln in lines)
            source_lines = None
            if preserve_sequence:
                source_lines = []
                offset = 0
                for line in lines:
                    source_lines.append(
                        {
                            "start": offset,
                            "end": offset + len(line.text),
                            "bbox": line.bbox,
                            "max_font_size": line.max_size,
                            "bold_ratio": line.bold_ratio,
                            "is_furniture": line.is_furniture,
                            "source_id": line.source_id,
                        }
                    )
                    offset += len(line.text) + 1
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
                    is_furniture=(
                        native_furniture[(pno, lines[0].block_no)]
                        if native_furniture
                        else all(ln.is_furniture for ln in lines)
                    ),
                    source_line_ids=([ln.source_id for ln in lines] if preserve_sequence else None),
                    source_lines=source_lines,
                )
            )
    return blocks
