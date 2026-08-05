"""DOCX extraction via python-docx (F1.2) — same contract as pdf.py.

A DOCX has no fixed pages until rendered, so anchors are body-item ordinals
(`anchor_kind="paragraph"`); reports cite the section path instead of a page
number. Word heading styles are the primary structure signal; documents
that never touch the style gallery (most student manuscripts) fall back to
the same bold + numbering heuristics as the PDF path.

Tracked changes: python-docx returns only text that is not part of a
pending revision, so unaccepted insertions are invisible here — authors
should submit clean copies (recorded limitation, ticket edge case).

CPU-bound and synchronous on purpose — callers run it in a threadpool.
"""

import re
import zipfile
from zipfile import BadZipFile

import docx
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.config import Settings
from app.errors import FileMalformedError, FileTooLargeError
from app.ingest import sections
from app.ingest.normalize import normalize
from app.ingest.patterns import HeadingPatterns, load_patterns
from app.ingest.schemas import (
    ExtractionResult,
    ImageBlock,
    PageGeometry,
    SectionTree,
    TableBlock,
    TextBlock,
)
from app.messages import DOCX_EXPANDS_TOO_LARGE, FILE_UNREADABLE

EMU_PER_POINT = 12700  # OOXML length unit (true invariant)
# Word's built-in style names are part of the format, not a rubric choice.
_HEADING_STYLE = re.compile(r"^heading (?P<level>\d+)$")
_TITLE_STYLE = "title"


def _check_uncompressed_size(path: str, settings: Settings) -> None:
    """A DOCX is a zip archive — `max_upload_mb` only caps the compressed
    size on disk. Reject before python-docx ever unpacks it if the
    UNCOMPRESSED total exceeds the configured ceiling (BUG-005/D-020:
    zip-bomb class risk against Render's 512MB free-tier memory)."""
    limit = settings.max_docx_uncompressed_mb * 1024 * 1024
    total = 0
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            total += info.file_size
            if total > limit:
                raise FileTooLargeError(DOCX_EXPANDS_TOO_LARGE)


def extract_document(path: str, settings: Settings) -> ExtractionResult:
    patterns = load_patterns(settings.ingest_patterns_file)
    try:
        _check_uncompressed_size(path, settings)
        document = docx.Document(path)
    except (PackageNotFoundError, BadZipFile, KeyError, ValueError) as exc:
        raise FileMalformedError(FILE_UNREADABLE) from exc

    blocks: list[TextBlock] = []
    tables: list[TableBlock] = []
    images: list[ImageBlock] = []
    candidates: list[sections.Candidate] = []
    used_styles = False

    for index, item in enumerate(document.iter_inner_content()):
        if isinstance(item, Table):
            tables.append(
                TableBlock(
                    paragraph=index,
                    rows=[[normalize(cell.text) for cell in row.cells] for row in item.rows],
                )
            )
            continue
        if not isinstance(item, Paragraph):
            continue

        # Images are anchored to the body item that carries their drawing.
        for _ in item._p.xpath(".//w:drawing | .//w:pict"):
            images.append(ImageBlock(paragraph=index))

        text = normalize(item.text)
        if not text:
            continue
        bold_ratio, max_size = _run_typography(item)
        blocks.append(
            TextBlock(
                paragraph=index,
                text=text,
                max_font_size=max_size,
                bold_ratio=bold_ratio,
            )
        )

        style_name = (item.style.name or "").casefold() if item.style else ""
        if style_name.startswith(patterns.toc_style_prefixes):
            continue  # a TOC listing is content, never structure
        candidate = _classify_paragraph(text, index, style_name, bold_ratio, patterns, settings)
        if candidate is not None:
            candidates.append(candidate)
            if _HEADING_STYLE.match(style_name) or style_name == _TITLE_STYLE:
                used_styles = True

    text_chars = sum(len(b.text) for b in blocks)
    if candidates:
        tree = SectionTree(
            source="styles" if used_styles else "heuristics",
            nodes=sections.assemble(candidates),
        )
    else:
        tree = SectionTree(source="none", nodes=[])

    return ExtractionResult(
        page_count=0,  # pages don't exist in an unrendered DOCX
        anchor_kind="paragraph",
        # No pages to average over: the whole body must clear the same
        # per-page bar a scan would (an image-only DOCX has ~0 chars).
        image_only=text_chars < settings.ingest_image_only_chars_per_page,
        text_chars=text_chars,
        section_tree=tree,
        blocks=blocks,
        images=images,
        tables=tables,
        geometry=_section_geometry(document),
    )


def _run_typography(paragraph: Paragraph) -> tuple[float, float]:
    """(bold_ratio, max_size_pt). Run properties inherit: `bold is None`
    means "whatever the style says", so the style's bold fills the gap.
    Sizes are usually None (inherited) — bold is the usable DOCX signal."""
    style_bold = bool(paragraph.style and paragraph.style.font and paragraph.style.font.bold)
    chars = bold_chars = 0
    max_size = 0.0
    for run in paragraph.runs:
        run_text = run.text
        if not run_text.strip():
            continue
        chars += len(run_text)
        effective_bold = run.bold if run.bold is not None else style_bold
        if effective_bold:
            bold_chars += len(run_text)
        if run.font.size is not None:
            max_size = max(max_size, run.font.size.pt)
    return (bold_chars / chars if chars else 0.0, max_size)


def _classify_paragraph(
    text: str,
    index: int,
    style_name: str,
    bold_ratio: float,
    patterns: HeadingPatterns,
    settings: Settings,
) -> "sections.Candidate | None":
    m = _HEADING_STYLE.match(style_name)
    if m:
        # The author's declared structure wins; numbering is still parsed
        # from the text so nodes stay comparable with the PDF path.
        return sections.Candidate(
            level=int(m.group("level")),
            title=text,
            paragraph=index,
            numbering=sections.numbering_of(text, patterns),
        )
    if style_name == _TITLE_STYLE:
        return sections.Candidate(level=1, title=text, paragraph=index, numbering=None)
    # Heuristic fallback, mirroring the PDF path: bold nominates, the
    # numbering/keyword patterns decide. Dot-leader lines are manual TOCs.
    if (
        bold_ratio >= settings.ingest_bold_ratio_min
        and len(text) <= settings.ingest_heading_max_chars
        and not patterns.toc_line_suffix.search(text)
    ):
        hit = sections.classify_heading(text, patterns)
        if hit is not None:
            return sections.Candidate(level=hit[0], title=text, paragraph=index, numbering=hit[1])
    return None


def _section_geometry(document: "docx.document.Document") -> list[PageGeometry]:
    """One entry per Word section — the declared page setup (F3.2 input)."""
    out: list[PageGeometry] = []
    for ordinal, section in enumerate(document.sections, start=1):
        if section.page_width is None or section.page_height is None:
            continue
        out.append(
            PageGeometry(
                docx_section=ordinal,
                width=section.page_width / EMU_PER_POINT,
                height=section.page_height / EMU_PER_POINT,
                margins=(
                    (section.left_margin or 0) / EMU_PER_POINT,
                    (section.top_margin or 0) / EMU_PER_POINT,
                    (section.right_margin or 0) / EMU_PER_POINT,
                    (section.bottom_margin or 0) / EMU_PER_POINT,
                ),
            )
        )
    return out
