"""Section-tree construction (F1.4) — the chapter map every later check
navigates by.

Two strategies, applied in order of evidence quality:

1. **Embedded TOC** (the document's own outline metadata), used only after
   validation: a minimum share of its entries must actually be findable on
   their destination pages (whitespace-insensitive containment). A TOC the
   document contradicts is discarded, not trusted.
2. **Heuristic scan** of the laid-out text: bold/oversized lines matching
   numbering patterns. Font-size popularity alone is NOT sufficient — the
   V0 demo document's headings are body-sized bold — so typography only
   nominates candidates and the numbering/keyword patterns decide.

Pages that *list* headings (table of contents, list of figures) and
repeating page furniture are excluded before scanning — both proven fake-
heading sources on the demo document. Patterns come from the data file
(patterns.py); numeric thresholds from Settings (ENGINEERING.md §8).
"""

from dataclasses import dataclass

from app.config import Settings
from app.ingest.normalize import match_key, normalize
from app.ingest.patterns import HeadingPatterns
from app.ingest.schemas import Line, SectionNode, SectionTree


@dataclass
class Candidate:
    """A heading nominee before tree assembly — shared by the PDF and DOCX
    extractors (one anchor field set per format, matching schemas.py)."""

    level: int
    title: str
    page: int | None = None
    paragraph: int | None = None
    numbering: str | None = None


def build_section_tree(
    lines_by_page: dict[int, list[Line]],
    embedded_toc: list[tuple[int, str, int]],
    page_keys: dict[int, str],
    patterns: HeadingPatterns,
    settings: Settings,
) -> SectionTree:
    """`page_keys` maps 1-based page number -> match_key of the page text."""
    toc_candidates = _clean_embedded_toc(embedded_toc, patterns)
    if len(toc_candidates) >= settings.ingest_toc_min_entries and _toc_matches_document(
        toc_candidates, page_keys, settings
    ):
        return SectionTree(source="embedded_toc", nodes=assemble(toc_candidates))

    heuristic = _heading_candidates(lines_by_page, patterns, settings)
    if heuristic:
        return SectionTree(source="heuristics", nodes=assemble(heuristic))
    return SectionTree(source="none", nodes=[])


# --- strategy 1: embedded TOC ------------------------------------------------


def _clean_embedded_toc(
    toc: list[tuple[int, str, int]], patterns: HeadingPatterns
) -> list[Candidate]:
    """Outline metadata is authored by tools and people — treat it as dirty
    input: drop blank titles, caption entries (figures/tables are not
    sections), bad page numbers, and consecutive duplicates."""
    out: list[Candidate] = []
    seen: set[tuple[str, int]] = set()
    for level, raw_title, page in toc:
        title = normalize(raw_title)
        if not title or page < 1 or patterns.caption.match(title):
            continue
        key = (match_key(title), page)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Candidate(
                level=max(1, level),
                title=title,
                page=page,
                numbering=numbering_of(title, patterns),
            )
        )
    return out


def numbering_of(title: str, patterns: HeadingPatterns) -> str | None:
    for pat in (patterns.chapter, patterns.numbered):
        m = pat.match(title)
        if m:
            return m.group("num")
    return None


def _toc_matches_document(
    candidates: list[Candidate], page_keys: dict[int, str], settings: Settings
) -> bool:
    """Quoted evidence must exist (PLAYBOOK §5): an outline entry counts as
    verified only if its text occurs on the page it points to."""
    if not candidates:
        return False
    found = sum(
        1
        for c in candidates
        if match_key(c.title) and match_key(c.title) in page_keys.get(c.page, "")
    )
    return found / len(candidates) >= settings.ingest_toc_match_min_ratio


# --- strategy 2: heuristic scan ----------------------------------------------


def toc_page_numbers(
    lines_by_page: dict[int, list[Line]], patterns: HeadingPatterns, settings: Settings
) -> set[int]:
    """Pages that list headings rather than contain them: a page titled from
    `toc_titles` starts a region; the region continues while pages keep the
    dot-leader/trailing-page-number shape."""
    toc_pages: set[int] = set()
    in_region = False
    for page in sorted(lines_by_page):
        lines = [ln for ln in lines_by_page[page] if ln.text]
        if not lines:
            in_region = False
            continue
        titled = any(normalize(ln.text).casefold() in patterns.toc_titles for ln in lines)
        listing_ratio = sum(1 for ln in lines if patterns.toc_line_suffix.search(ln.text)) / len(
            lines
        )
        if titled:
            in_region = True
        elif in_region and listing_ratio < settings.ingest_toc_line_ratio:
            in_region = False
        if in_region:
            toc_pages.add(page)
    return toc_pages


def _body_font_size(lines_by_page: dict[int, list[Line]]) -> float:
    """The document's body size = the size carrying the most characters."""
    weight: dict[float, int] = {}
    for lines in lines_by_page.values():
        for ln in lines:
            sz = round(ln.max_size, 1)
            weight[sz] = weight.get(sz, 0) + len(ln.text)
    return max(weight, key=lambda s: weight[s]) if weight else 0.0


def _heading_candidates(
    lines_by_page: dict[int, list[Line]], patterns: HeadingPatterns, settings: Settings
) -> list[Candidate]:
    body_size = _body_font_size(lines_by_page)
    skip_pages = toc_page_numbers(lines_by_page, patterns, settings)
    out: list[Candidate] = []

    def styled(ln: Line) -> bool:
        oversized = ln.max_size >= body_size + settings.ingest_heading_size_delta
        # -0.1 = float noise tolerance on "at least body-sized", not a knob:
        # bold text smaller than body (fine print) must not read as a heading.
        bold = ln.bold_ratio >= settings.ingest_bold_ratio_min and ln.max_size >= body_size - 0.1
        return (oversized or bold) and 0 < len(ln.text) <= settings.ingest_heading_max_chars

    for page in sorted(lines_by_page):
        if page in skip_pages:
            continue
        lines = [ln for ln in lines_by_page[page] if not ln.is_furniture and ln.text]
        i = 0
        while i < len(lines):
            ln = lines[i]
            i += 1
            if not styled(ln):
                continue
            cand = _classify(ln, patterns)
            if cand is None:
                continue
            # Multi-line chapter headings ("CHAPTER 2" / next line carries
            # the title) — merge immediate styled continuation lines that
            # are not themselves numbered headings.
            if (
                cand.numbering is not None
                and patterns.chapter.match(ln.text)
                and not _chapter_has_title(ln.text, patterns)
            ):
                while i < len(lines) and styled(lines[i]) and _classify(lines[i], patterns) is None:
                    cand.title = f"{cand.title} {lines[i].text}".strip()
                    i += 1
            prev = out[-1] if out else None
            if prev and prev.title == cand.title and prev.page == cand.page:
                continue  # layout artifacts can repeat a heading line
            out.append(cand)
    return out


def _chapter_has_title(text: str, patterns: HeadingPatterns) -> bool:
    m = patterns.chapter.match(text)
    return bool(m and m.group("title").strip())


def classify_heading(text: str, patterns: HeadingPatterns) -> tuple[int, str | None] | None:
    """(level, numbering) for a heading-shaped text, else None — the shared
    classifier the DOCX extractor uses for documents without Word styles."""
    c = _classify_text(text, 0, patterns)
    return (c.level, c.numbering) if c else None


def heading_shaped(text: str, patterns: HeadingPatterns) -> bool:
    """Whether a line's text matches any heading pattern — used by the
    furniture detector to avoid eating one-off headings that sit in the
    header/footer band (chapters routinely start at the very top of a page)."""
    return _classify_text(text, 0, patterns) is not None


def _classify(ln: Line, patterns: HeadingPatterns) -> "Candidate | None":
    return _classify_text(ln.text, ln.page, patterns)


def _classify_text(text: str, page: int, patterns: HeadingPatterns) -> "Candidate | None":
    """Assign a level from numbering/keywords; typography alone never
    qualifies a line (bold names on a title page are not sections)."""
    m = patterns.chapter.match(text)
    if m:
        return Candidate(level=1, title=text, page=page, numbering=m.group("num"))
    m = patterns.numbered.match(text)
    if m:
        num = m.group("num")
        return Candidate(level=num.count(".") + 1, title=text, page=page, numbering=num)
    if normalize(text).casefold() in patterns.unnumbered_sections:
        return Candidate(level=1, title=text, page=page, numbering=None)
    m = patterns.top_numbered.match(text)
    if m:
        return Candidate(level=1, title=text, page=page, numbering=m.group("num"))
    return None


# --- shared tree assembly ----------------------------------------------------


def assemble(candidates: list[Candidate]) -> list[SectionNode]:
    """Stack-based nesting; level jumps are clamped to parent+1 so a noisy
    outline can never produce an orphaned depth."""
    roots: list[SectionNode] = []
    stack: list[tuple[int, SectionNode]] = []
    for c in candidates:
        while stack and stack[-1][0] >= c.level:
            stack.pop()
        level = stack[-1][0] + 1 if stack else 1
        node = SectionNode(
            title=c.title,
            level=level,
            page=c.page,
            paragraph=c.paragraph,
            numbering=c.numbering,
        )
        if stack:
            stack[-1][1].children.append(node)
        else:
            roots.append(node)
        stack.append((level, node))
    return roots
