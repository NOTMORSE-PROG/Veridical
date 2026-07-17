"""Reference-list extraction into structured citations (F1.5).

Runs on the format-neutral `ExtractionResult`, so PDFs and DOCX share one
implementation: locate the references section through the section tree,
segment it into entries, parse each entry.

Segmentation:
- paragraph anchors (DOCX): every body paragraph in the section is one
  entry — Word already owns the line wrapping.
- page anchors (PDF): APA hanging indent — an entry starts at the section's
  leftmost x0; more-indented blocks are continuations. When every block
  sits at the same x0 (no hanging indent) each block is its own entry.

Parsing is deliberately regex-only: measured 17/17 correct on the V0 demo
document's reference list (ticket evidence), so the researched Gemini
fallback (one structured-output call) stays unbuilt until a fixture proves
the need — quota is the currency. The APA shape regexes below are format
invariants of the citation style, not tunable thresholds; the section
*titles* that name a reference list are data (heading_patterns.json).

A failed parse NEVER drops the entry: raw text is preserved with
`parse_failed` so the citation checks can show it for manual review.
"""

import re
from dataclasses import dataclass, field

from app.ingest.normalize import match_key, normalize
from app.ingest.patterns import HeadingPatterns
from app.ingest.schemas import ExtractionResult, SectionNode, TextBlock
from app.models.enums import CitationParseStatus

# APA-shape invariants. YEAR is the entry's backbone: "(2024)." with
# optional letter suffix ("2024a") or month/day detail ("2025, June 28").
_YEAR = re.compile(r"\((\d{4})[a-z]?(?:,[^)]*)?\)")
_DOI = re.compile(r"\b10\.\d{4,9}/[^\s]+")
_ISBN = re.compile(r"\bISBN[:\s-]*((?:[\dXx][- ]?){10,17})", re.IGNORECASE)
_URL = re.compile(r"https?://\S+")
# Sentence split that survives "CMO No. 15" / "Vol. 3": only a period
# followed by whitespace and a capital or opening paren starts a new part.
# "arXiv" is the one common venue that starts lowercase (APA preprint
# format: "Title (arXiv:id). arXiv.") — named explicitly.
_PART_SPLIT = re.compile(r"\.\s+(?=[A-Z(]|arXiv\b)")
# Author-list separators: "A, B., & C." — split before the next surname.
_AUTHOR_SPLIT = re.compile(r",\s+(?=[A-Z][\w'’-]+,)|,?\s+&\s+")
_TRAILING_PUNCT = ".,;:)"


@dataclass
class CitationDraft:
    """Parser output; persisted 1:1 as a `citation` row (models/citation.py)."""

    order_index: int
    raw_text: str
    authors: list[str] | None = None
    year: int | None = None
    title: str | None = None
    venue: str | None = None
    doi: str | None = None
    isbn: str | None = None
    url: str | None = None
    parse_status: CitationParseStatus = field(default=CitationParseStatus.parse_failed)


def extract_references(result: ExtractionResult, patterns: HeadingPatterns) -> list[CitationDraft]:
    span = _reference_span(result, patterns)
    entries = _segment(span, result.anchor_kind)
    return [parse_reference(raw, i) for i, raw in enumerate(entries)]


def parse_reference(raw: str, order_index: int) -> CitationDraft:
    """Best-effort APA parse. Never raises: any surprise leaves the entry
    as parse_failed with its raw text intact."""
    draft = CitationDraft(order_index=order_index, raw_text=raw)
    try:
        _parse_into(draft, raw)
    except Exception:  # noqa: BLE001 — the QA property "never throws" outranks
        draft.parse_status = CitationParseStatus.parse_failed
    return draft


def _parse_into(draft: CitationDraft, raw: str) -> None:
    doi = _DOI.search(raw)
    if doi:
        draft.doi = doi.group(0).rstrip(_TRAILING_PUNCT)
    isbn = _ISBN.search(raw)
    if isbn:
        draft.isbn = re.sub(r"[- ]", "", isbn.group(1)).upper()
    url = _URL.search(raw)
    if url:
        draft.url = url.group(0).rstrip(_TRAILING_PUNCT)

    year_m = _YEAR.search(raw)
    if not year_m:
        return  # no year backbone -> parse_failed, raw preserved
    draft.year = int(year_m.group(1))

    authors_raw = raw[: year_m.start()].strip()
    # The segment's closing period belongs to a trailing initial ("Cruz,
    # M. A. (2023)") — strip it only when it isn't one (corporate authors:
    # "Commission on Higher Education. (2019)").
    if authors_raw and not re.search(r"\b[A-Z]\.$", authors_raw):
        authors_raw = authors_raw.rstrip(".")
    if authors_raw:
        draft.authors = [a.strip() for a in _AUTHOR_SPLIT.split(authors_raw) if a.strip()]

    rest = raw[year_m.end() :].lstrip(". ").strip()
    # The URL/DOI tail is locator, not venue text.
    rest = _URL.sub("", rest).strip()
    parts = _PART_SPLIT.split(rest, maxsplit=1)
    title = parts[0].strip().rstrip(".")
    if title:
        draft.title = title
        draft.venue = parts[1].strip().rstrip(".") or None if len(parts) > 1 else None
    if draft.authors and draft.title:
        draft.parse_status = CitationParseStatus.parsed


# --- locating and segmenting the section -------------------------------------


def _flatten(nodes: list[SectionNode]) -> list[SectionNode]:
    out: list[SectionNode] = []
    for n in nodes:
        out.append(n)
        out.extend(_flatten(n.children))
    return out


def _reference_span(result: ExtractionResult, patterns: HeadingPatterns) -> list[TextBlock]:
    """Blocks strictly between the references heading and the next heading
    of the same or higher rank (document order); [] when no reference
    section exists — that is a fact for F5 to report, not an error."""
    flat = _flatten(result.section_tree.nodes)
    ref_pos = next(
        (
            i
            for i, n in enumerate(flat)
            if normalize(n.title).casefold() in patterns.reference_titles
        ),
        None,
    )
    blocks = [b for b in result.blocks if not b.is_furniture]
    if ref_pos is None:
        return _span_by_bare_heading(blocks, patterns)
    node = flat[ref_pos]
    successor = next((n for n in flat[ref_pos + 1 :] if n.level <= node.level), None)

    start = _block_index_of_heading(blocks, node)
    if start is None:
        return []
    end = len(blocks)
    if successor is not None:
        succ_idx = _block_index_of_heading(blocks, successor, after=start)
        if succ_idx is not None:
            end = succ_idx
    return blocks[start + 1 : end]


def _block_index_of_heading(
    blocks: list[TextBlock], node: SectionNode, after: int = -1
) -> int | None:
    """The block that IS the heading — matched by anchor when available,
    text containment as the fallback."""
    want = match_key(node.title)
    for i, b in enumerate(blocks):
        if i <= after:
            continue
        if node.paragraph is not None and b.paragraph == node.paragraph:
            return i
        if node.page is not None and b.page == node.page and want in match_key(b.text):
            return i
    return None


def _span_by_bare_heading(blocks: list[TextBlock], patterns: HeadingPatterns) -> list[TextBlock]:
    """Tree didn't include the reference section (e.g. source='none'):
    fall back to a standalone line naming it."""
    for i, b in enumerate(blocks):
        if normalize(b.text).casefold() in patterns.reference_titles:
            return blocks[i + 1 :]
    return []


def _segment(span: list[TextBlock], anchor_kind: str) -> list[str]:
    if not span:
        return []
    if anchor_kind == "paragraph":
        return [_join_lines(b.text) for b in span if b.text.strip()]

    # Page anchors: hanging indent. The section's leftmost x0 is where
    # entries start; anything to the right continues the previous entry.
    xs = [b.bbox[0] for b in span if b.bbox is not None]
    entry_x = min(xs) if xs else 0.0
    entries: list[str] = []
    current: list[str] = []
    for b in span:
        x0 = b.bbox[0] if b.bbox is not None else entry_x
        # 3pt: rendering jitter on identical indents, far below any real
        # hanging indent (~0.25in = 18pt) — layout invariant, not a knob.
        if x0 <= entry_x + 3.0 and current:
            entries.append(_merge(current))
            current = []
        current.append(b.text)
    if current:
        entries.append(_merge(current))
    return [e for e in entries if e]


def _merge(parts: list[str]) -> str:
    return _join_lines("\n".join(parts))


def _join_lines(text: str) -> str:
    """Rejoin wrapped lines. A line that breaks inside a URL is glued
    without a space (verified on the demo document: a wrapped
    rsisinternational.org URL), prose lines get one."""
    out = ""
    for line in (ln.strip() for ln in text.split("\n")):
        if not line:
            continue
        if not out:
            out = line
        elif "://" in out.rsplit(None, 1)[-1] and not out.endswith((".", ",", ";")):
            out += line
        else:
            out += " " + line
    return normalize(out)
