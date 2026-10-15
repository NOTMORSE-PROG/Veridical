"""Reference-list extraction into structured citations (F1.5).

Runs on the format-neutral `ExtractionResult`, so PDFs and DOCX share one
implementation: locate the references section through the section tree,
segment it into entries, parse each entry.

Segmentation:
- paragraph anchors (DOCX): every body paragraph in the section is one
  entry — Word already owns the line wrapping.
- page anchors (PDF): sequential printed reference numbers take precedence
  when present. Otherwise APA hanging indent starts an entry at the
  section's leftmost x0; more-indented blocks are continuations. When every
  block sits at the same x0 (no hanging indent) each block is its own entry.

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
from statistics import median

from app.config import get_settings
from app.ingest.normalize import match_key, normalize
from app.ingest.patterns import HeadingPatterns
from app.ingest.reference_urls import reference_urls, text_url
from app.ingest.schemas import ExtractionResult, SectionNode, TextBlock
from app.ingest.source_slices import SourceFragment, SourceSlicer, line_inventory_valid, slice_block
from app.models.enums import CitationParseStatus

# APA-shape invariants. YEAR is the entry's backbone: "(2024)." with
# optional letter suffix ("2024a") or month/day detail ("2025, June 28").
_YEAR = re.compile(r"\((\d{4})[a-z]?(?:,[^)]*)?\)")
_AUTHOR_YEAR_ENTRY_START = re.compile(r"^[^()\n]+\(\d{4}[a-z]?(?:,[^)]*)?\)\.\s*\S")
# Numeric publication metadata is not an APA date, even if the issue has
# four digits that could be a year. Do not invent a date for an unsupported
# reference style; keep the original entry available for verification.
_VOLUME_ISSUE_PAGES = re.compile(r"\b\d+\s*\((?P<issue>\d+)\)\s*[:,]\s*\d+")
_DOI = re.compile(r"\b10\.\d{4,9}/[^\s]+")
_ISBN = re.compile(r"\bISBN[:\s-]*((?:[\dXx][- ]?){10,17})", re.IGNORECASE)
_URL = re.compile(r"https?://\S+")
# Sentence split that survives "CMO No. 15" / "Vol. 3": only a period
# followed by whitespace and a capital or opening paren starts a new part.
# "arXiv" is the one common venue that starts lowercase (APA preprint
# format: "Title (arXiv:id). arXiv.") — named explicitly.
_PART_SPLIT = re.compile(r"\.\s+(?=[A-Z(]|arXiv\b)")
# A numbered bibliography may omit the journal name and place only a
# volume(issue):pages tail after a lowercase title. This narrowly shaped
# suffix is publication metadata, not title-search text.
_BARE_VOLUME_PAGES = re.compile(
    r"^(?P<title>.+?)\.\s+(?P<tail>\d+(?:\(\d+\))?:\s*\d+(?:\s*[-\u2013]\s*\d+)?)$",
    re.DOTALL,
)
# Author-list separators: "A, B., & C." — split before the next surname.
_AUTHOR_SPLIT = re.compile(r",\s+(?=[A-Z][\w'’-]+,)|,?\s+&\s+")
_NUMBERED_ENTRY = re.compile(
    r"^\s*(?:([1-9]\d*)[.)]|\[([1-9]\d*)\])(?:\s+(.+)|([A-Z].*))", re.DOTALL
)
_STANDALONE_REFERENCE_LABEL = re.compile(r"^\s*(?:\[[1-9]\d*\]|[1-9]\d*[.)])\s*$")
_REFERENCE_MARKER = re.compile(r"^\s*(?:([1-9]\d*)[.)]|\[([1-9]\d*)\])(?:\s|(?=[A-Z])|$)")
# A title may begin with opening quotation marks (or a replacement glyph
# produced when the PDF font has no Unicode map). Keep the following letter
# requirement so a DOI fragment or page number cannot start a new entry.
_ENTRY_OPENING_QUOTES = "\"'`\u00b4\u201c\u2018\u00ab\u2039\u201e\u201a\ufffd \t\r\n"
# IEEE repeated-author dashes precede a comma and a quoted title. They
# identify a list entry, never an author name to reconstruct or guess.
_ENTRY_AUTHOR_REPEAT = re.compile(r"^[\u2014\u2015]+,?\s*")
_TRAILING_PUNCT = ".,;:)"


def possible_split_doi(raw_text: str, extracted_doi: str) -> bool:
    """Whether a stored DOI may stop before a whitespace-separated tail.

    DOI names may legitimately contain spaces. Once source layout is
    flattened, a following word, number, or punctuation-led tail may be
    ordinary reference metadata OR part of the DOI; do not turn a negative
    prefix lookup into a finding.
    This deliberately trades coverage for precision and never reconstructs an
    identifier. A terminal DOI remains eligible for normal verification.
    """
    return bool(
        re.search(
            rf"(?<!\w){re.escape(extracted_doi)}[.,;:]?\s+(?=(?:[./:_-]\s*)?\w)",
            raw_text,
        )
    )


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


@dataclass(frozen=True)
class ReferenceEntryLocation:
    """Source location re-derived from the stored extraction, never guessed by row order."""

    index: int
    raw_text: str
    printed_number: int | None
    page: int | None
    paragraph: int | None
    bboxes: tuple[tuple[float, float, float, float], ...]


@dataclass(frozen=True)
class ReferenceSelection:
    """One source-bound membership decision shared by all bibliography consumers."""

    groups: list[list[TextBlock]]
    span: list[TextBlock]
    issues: tuple[str, ...] = ()


def select_references(result: ExtractionResult, patterns: HeadingPatterns) -> ReferenceSelection:
    projected: list[TextBlock] = []
    for index, block in enumerate(result.blocks):
        if block.is_furniture or not block.text:
            continue
        if not line_inventory_valid(block):
            # The invalid inventory cannot safely remove characters from the
            # bibliography or promote them into body citation claims.
            return ReferenceSelection([], [], ("invalid_source_line_inventory",))
        fragment = SourceFragment(
            **block.model_dump(),
            source_block_index=index,
            source_start=0,
            source_end=len(block.text),
        )
        slicer = SourceSlicer(fragment)
        start = 0
        for line in block.source_lines or []:
            if not line.is_furniture:
                continue
            if block.text[start : line.start].strip():
                projected.append(slicer.slice(start, line.start))
            start = line.end
            while start < len(block.text) and block.text[start] in "\r\n":
                start += 1
        if block.text[start:].strip():
            projected.append(slicer.slice(start, len(block.text)))
    issues: list[str] = []
    span = _reference_span_blocks(result.model_copy(update={"blocks": projected}), patterns, issues)
    for block in span:
        for line in block.text.splitlines():
            marker = _REFERENCE_MARKER.match(line)
            if marker and _parse_reference_number(marker.group(1) or marker.group(2)) is None:
                return ReferenceSelection([], span, (*issues, "unreadable_reference_number"))
    groups = _segment_blocks(span, result.anchor_kind)
    # These are candidate markers, not proof that an ambiguous numbered line
    # is a reference rather than quoted title text. Do not silently merge it.
    if result.anchor_kind == "page":
        style = next(
            (_number_marker_style(b.text) for b in span if _contextual_reference_number(b.text)),
            None,
        )
        candidates = _split_numbered_block_lines(span) if style else []
        labels = [
            _contextual_reference_number(b.text)
            for b in candidates
            if _number_marker_style(b.text) == style
            and _contextual_reference_number(b.text) is not None
        ]
        # Positive segmentation requires consecutive markers. Contradictory
        # markers need no such prerequisite: [1],[1] is already not unique.
        if len(labels) != len(set(labels)):
            issues.append("ambiguous_duplicate_reference_number")
        elif labels != sorted(labels):
            issues.append("ambiguous_reference_number_sequence")
        else:
            consumed = [
                _contextual_reference_number(group[0].text)
                for group in groups
                if _number_marker_style(group[0].text) == style
                and _contextual_reference_number(group[0].text) is not None
            ]
            if labels != consumed:
                issues.append("ambiguous_reference_entry_boundaries")
    return ReferenceSelection(groups, span, tuple(issues))


def extract_references(result: ExtractionResult, patterns: HeadingPatterns) -> list[CitationDraft]:
    selection = select_references(result, patterns)
    if selection.issues or reference_reading_order_issues(result, selection):
        return []
    groups = selection.groups
    urls = reference_urls(
        groups,
        result.uri_links,
        complete=result.uri_links_complete,
        tolerance=patterns.reference_url_geometry_tolerance_pts,
    )
    drafts = [
        parse_reference(_merge([block.text for block in group]), i)
        for i, group in enumerate(groups)
    ]
    for draft, url in zip(drafts, urls, strict=True):
        draft.url = url
    return drafts


def reference_entry_locations(
    result: ExtractionResult, patterns: HeadingPatterns
) -> list[ReferenceEntryLocation]:
    """Locate each segmented entry for conservative manuscript navigation.

    The position alone is insufficient for old runs parsed with a different
    boundary algorithm. Callers must also compare the stored flag's quoted
    reference text to ``raw_text`` before showing an exact page or box.
    """
    selection = select_references(result, patterns)
    if selection.issues or reference_reading_order_issues(result, selection):
        return []
    groups = selection.groups
    locations: list[ReferenceEntryLocation] = []
    for index, group in enumerate(groups, start=1):
        first = group[0]
        locations.append(
            ReferenceEntryLocation(
                index=index,
                raw_text=_merge([block.text for block in group]),
                printed_number=_contextual_reference_number(first.text),
                page=first.page,
                paragraph=first.paragraph,
                bboxes=tuple(
                    block.bbox
                    for block in group
                    if block.page == first.page and block.bbox is not None
                )
                if all(block.bbox is not None for block in group if block.page == first.page)
                else (),
            )
        )
    return locations


def reference_reading_order_issues(
    result: ExtractionResult, selection: ReferenceSelection
) -> list[dict[str, int | str]]:
    """Validate a complete bibliography scope, including its source boundaries.

    The selected fragments retain their original block indices. Include the
    preceding/following nonempty source blocks so an unsupported heading or
    closing boundary cannot make a partial list appear complete. Also include
    intervening pages, even when no text was recovered on them. Global metadata
    and source-identity defects remain blocking in ``reading_order_issues``.

    An empty selection cannot establish absence in a partly unreadable document.
    Other consumers must still disclose document-wide citation/body coverage.
    """
    from app.ingest.quality import reading_order_issues

    if not selection.span or result.anchor_kind != "page":
        return reading_order_issues(result)
    if not all(isinstance(block, SourceFragment) for block in selection.span):
        return reading_order_issues(result)
    first = min(selection.span, key=lambda block: (block.source_block_index, block.source_start))
    last = max(selection.span, key=lambda block: (block.source_block_index, block.source_end))
    start, end = first.source_block_index, last.source_block_index
    # A split proves a local boundary only when the excluded source contains
    # non-furniture text. Removing a page number or blank line proves nothing
    # about where the bibliography starts or finishes.
    if not _has_body_text(result.blocks[start], 0, first.source_start):
        start = next(
            (
                index
                for index in range(start - 1, -1, -1)
                if result.blocks[index].text.strip() and not result.blocks[index].is_furniture
            ),
            start,
        )
    has_closing_text = _has_body_text(
        result.blocks[end], last.source_end, len(result.blocks[end].text)
    )
    if not has_closing_text:
        end = next(
            (
                index
                for index in range(end + 1, len(result.blocks))
                if result.blocks[index].text.strip() and not result.blocks[index].is_furniture
            ),
            end,
        )
    pages = {block.page for block in result.blocks[start : end + 1] if block.page is not None}
    if not pages:
        return reading_order_issues(result)
    scope_end = max(pages)
    if end == last.source_block_index and not has_closing_text:
        # EOF in the recovered text is not necessarily EOF in the PDF. A later
        # unreadable page could continue the list even when it yielded no block.
        scope_end = result.page_count
    return reading_order_issues(result, pages=set(range(min(pages), scope_end + 1)))


def _has_body_text(block: TextBlock, start: int, end: int) -> bool:
    """Whether an exact source interval contains more than blank/page furniture."""
    if block.source_lines is None:
        return bool(block.text[start:end].strip())
    return any(
        not line.is_furniture
        and max(start, line.start) < min(end, line.end)
        and block.text[max(start, line.start) : min(end, line.end)].strip()
        for line in block.source_lines
    )


def non_reference_blocks(
    result: ExtractionResult,
    patterns: HeadingPatterns,
    *,
    selection: ReferenceSelection | None = None,
) -> list[TextBlock]:
    """Exact complement of bibliography/furniture intervals, not object identity."""
    selection = selection or select_references(result, patterns)
    if selection.issues:
        return []
    excluded: dict[int, list[tuple[int, int]]] = {}
    for fragment in selection.span:
        excluded.setdefault(fragment.source_block_index, []).append(
            (fragment.source_start, fragment.source_end)
        )
    body = []
    for index, block in enumerate(result.blocks):
        if block.is_furniture or not block.text:
            continue
        intervals = list(excluded.get(index, []))
        intervals.extend(
            (line.start, line.end) for line in block.source_lines or [] if line.is_furniture
        )
        if not intervals:
            body.append(block)
            continue
        fragment = SourceFragment(
            **block.model_dump(),
            source_block_index=index,
            source_start=0,
            source_end=len(block.text),
        )
        slicer = SourceSlicer(fragment)
        start = 0
        for lower, upper in sorted(intervals):
            if lower > start and block.text[start:lower].strip():
                body.append(slicer.slice(start, lower))
            start = max(start, upper)
        if block.text[start:].strip():
            body.append(slicer.slice(start, len(block.text)))
    return body


def reference_section_page(result: ExtractionResult, patterns: HeadingPatterns) -> int | None:
    """First evidenced reference-list page for a manuscript deep link.

    A missing page anchor stays absent; callers must not guess a bibliography
    location from reference order or a fixed page number.
    """
    selection = select_references(result, patterns)
    return next((block.page for block in selection.span if block.page is not None), None)


def parse_reference(raw: str, order_index: int) -> CitationDraft:
    """Best-effort APA parse. Never raises: any surprise leaves the entry
    as parse_failed with its raw text intact."""
    draft = CitationDraft(order_index=order_index, raw_text=raw)
    # Bound structured inference, not the retained source. Oversized entries
    # remain available verbatim and cannot become verified metadata.
    if len(raw) > get_settings().reference_metadata_max_chars:
        return draft
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
    draft.url = text_url(raw)

    issue_spans = {match.span("issue") for match in _VOLUME_ISSUE_PAGES.finditer(raw)}
    year_m = next(
        (match for match in _YEAR.finditer(raw) if match.span(1) not in issue_spans), None
    )
    if not year_m:
        # IEEE-style numbered entries print the year at the end, not in
        # parentheses; their title is the quoted span.
        _parse_ieee_into(draft, raw)
        return  # otherwise no year backbone -> parse_failed, raw preserved
    draft.year = int(year_m.group(1))

    # A numbered APA list ("12. Cruz, M. A. (2023)") keeps its label in the
    # raw text; it is not part of the first author's name.
    authors_raw = _LEADING_LABEL.sub("", raw[: year_m.start()], count=1).strip()
    # The segment's closing period belongs to a trailing initial ("Cruz,
    # M. A. (2023)") — strip it only when it isn't one (corporate authors:
    # "Commission on Higher Education. (2019)").
    if authors_raw and not re.search(r"\b[A-Z]\.$", authors_raw):
        authors_raw = authors_raw.rstrip(".")
    if authors_raw:
        draft.authors = split_apa_authors(authors_raw)

    rest = raw[year_m.end() :].lstrip(". ").strip()
    # The URL/DOI tail is locator, not venue text: "https://doi.org/10...",
    # "doi:10..." and a bare "10.xxxx/..." alike, plus "Retrieved from".
    rest = _URL.sub("", rest)
    rest = _DOI_TAIL.sub("", rest)
    rest = _RETRIEVED.sub("", rest).strip()
    title, venue = _split_title_venue(rest)
    if venue is None and (publication_tail := _BARE_VOLUME_PAGES.fullmatch(title)):
        draft.title = publication_tail.group("title").strip()
        draft.venue = publication_tail.group("tail").strip()
        if draft.authors and draft.title:
            draft.parse_status = CitationParseStatus.parsed
        return
    if title:
        draft.title = title
        draft.venue = venue
    if draft.authors and draft.title:
        draft.parse_status = CitationParseStatus.parsed


# A list label in front of an entry: "[12] ", "12. ", "12) ".
_LEADING_LABEL = re.compile(r"^\s*(?:\[\d+\]|\d+[.)])\s*")
_DOI_TAIL = re.compile(r"\s*(?:\bdoi:?\s*)?\b10\.\d{4,9}/\S+", re.IGNORECASE)
# A trailing access note, never a phrase inside a title ("energies available
# at the CERN Large Hadron Collider"): anchored at the end, after URLs and DOIs
# have been removed.
_RETRIEVED = re.compile(
    r"\s*\b(?:Retrieved(?:\s+\w+\s+\d{1,2},\s+\d{4},)?\s+from|Available\s+(?:at|from)|Accessed(?:\s+on)?)"
    r"\s*:?[\w\s,./:\[\]\-]{0,60}$",
    re.IGNORECASE,
)
# "Venue, 8(3), 3999-4010" or "Venue, 12, 107223": a journal tail, used to
# find the title's end when the venue begins in lowercase ("bit-Tech").
_VENUE_TAIL = re.compile(
    r".+,\s*\d+(?:\s*\([^)]+\))?(?:,\s*[A-Za-z]?\d+(?:\s*[-–]\s*[A-Za-z]?\d+)?)?\s*$"
)
# End of an APA title: a period (or ?/!) followed by the venue, which may
# begin with a capital, a parenthesis or non-Latin script. A digit is only
# a candidate boundary: it needs explicit publication punctuation below.
_TITLE_END = re.compile(r"(?P<mark>[.?!])\s+(?=[A-Z(\d\[]|arXiv\b|[^\x00-\x7F])")
_PAGINATED_NUMERIC_VENUE = re.compile(
    r"(?P<venue>\d[^.!?]*?)(?:,\s*pp?\.\s*\d+(?:\s*[-\u2013]\s*\d+)?"
    r"|\s+\(pp?\.\s*\d+(?:\s*[-\u2013]\s*\d+)?\))$"
)
# An initials token in an author list: "J.", "J. P.", "J.-P.", "Ma."
_INITIALS = re.compile(
    r"(?:[A-ZÀ-Þ][a-zà-ÿ]?\.(?:\s*-?\s*[A-ZÀ-Þ][a-zà-ÿ]?\.)*|Jr\.|Sr\.|I{2,3}|IV)"
)


# "Battery vs. Conduction", "a review cf. earlier work": an abbreviation's
# period inside a title is not the title's end. (Whole word only: a title
# ending in "revisited." or "needed." does end there.)
_TITLE_ABBREVIATION = re.compile(
    r"(?:^|[\s(])(?:vs|cf|e\.g|i\.e|etc|al|no|vol|pp|ed|eds|approx)$", re.IGNORECASE
)


def _supported_numeric_venue(tail: str) -> bool:
    if not tail or not tail[0].isdigit():
        return True
    match = _PAGINATED_NUMERIC_VENUE.fullmatch(tail)
    return bool(match and any(char.isalpha() for char in match.group("venue")))


def _split_title_venue(rest: str) -> tuple[str, str | None]:
    unresolved_numeric_boundary = False
    for match in _TITLE_END.finditer(rest):
        before = rest[: match.start()]
        # After an ambiguous numeric split, no later candidate (even an
        # abbreviation) may discard that uncertainty and reach the bare-volume
        # caller fallback with a newly invented partial title.
        if unresolved_numeric_boundary:
            raise ValueError("ambiguous_numeric_title_boundary")
        # "U.S. Army", "J. Smith": a single capital before the period is an
        # abbreviation or initial inside the title, not the title's end.
        if match.group("mark") == "." and (
            re.search(r"(?:^|[\s.])[A-Z]$", before) or _TITLE_ABBREVIATION.search(before)
        ):
            continue
        venue = rest[match.end() :].strip().rstrip(".")
        if not _supported_numeric_venue(venue):
            unresolved_numeric_boundary = True
            continue
        title = before + (match.group("mark") if match.group("mark") != "." else "")
        return title.strip(), venue or None
    for match in reversed(list(re.finditer(r"\.\s+", rest))):
        tail = rest[match.end() :].strip().rstrip(".")
        if _supported_numeric_venue(tail) and _VENUE_TAIL.fullmatch(tail):
            if unresolved_numeric_boundary:
                raise ValueError("ambiguous_numeric_title_boundary")
            return rest[: match.start()].strip(), tail
    # Whole-string preservation is not proof that all of it is a title. Only
    # the separately supported exact volume/page grammar can resolve this
    # remaining numeric boundary; otherwise retain raw and withhold metadata.
    if unresolved_numeric_boundary and not _BARE_VOLUME_PAGES.fullmatch(rest.strip().rstrip(".")):
        raise ValueError("ambiguous_numeric_title_boundary")
    return rest.strip().rstrip("."), None


def split_apa_authors(authors_raw: str) -> list[str]:
    """Group "Surname, I. I." pairs; surnames may hold several words.

    The old split required each next surname to be ONE capitalized word, so
    "Gaber, A. A., Abo Elmaaty, A." and "van der Berg, J." merged authors and
    made the author count disagree with the provider record.
    """
    tokens = [
        t.strip()
        # "and" separates authors only right after initials: a corporate
        # author such as "Department of Science and Technology" stays whole.
        for t in re.split(r",\s*&\s*|\s+&\s+|(?<=\.),?\s+and\s+|,\s*", authors_raw)
        if t.strip()
    ]
    authors: list[str] = []
    for token in tokens:
        if authors and _INITIALS.fullmatch(token) and "," not in authors[-1].split(",", 1)[-1]:
            authors[-1] = f"{authors[-1]}, {token}"
        elif authors and _INITIALS.fullmatch(token):
            authors[-1] = f"{authors[-1]} {token}"
        else:
            authors.append(token)
    return authors


_IEEE_QUOTED_TITLE = re.compile(r"[\"“”](?P<title>[^\"“”]{3,}?)[,.]?[\"“”]")
_IEEE_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


def _ieee_author(name: str) -> str | None:
    """'A. B. Surname' -> 'Surname, A. B.' (the comparable APA order)."""
    words = name.replace(" ", " ").split()
    initials = []
    while words and _INITIALS.fullmatch(words[0]) and len(words) > 1:
        initials.append(words.pop(0))
    if not words:
        return None
    surname = " ".join(words)
    return f"{surname}, {' '.join(initials)}" if initials else surname


def _parse_ieee_into(draft: CitationDraft, raw: str) -> None:
    body = _LEADING_LABEL.sub("", raw, count=1)
    match = _IEEE_QUOTED_TITLE.search(body)
    if match is None:
        return
    authors_part = body[: match.start()].strip().rstrip(",").strip()
    after = _DOI_TAIL.sub("", _URL.sub("", body[match.end() :]))
    years = _IEEE_YEAR.findall(after)
    if not authors_part or not years:
        return
    names = [
        n.strip()
        for n in re.split(r",\s*(?:and\s+)?|\s+and\s+", re.sub(r"\bet\s+al\.?", "", authors_part))
        if n.strip()
    ]
    authors = [a for a in (_ieee_author(n) for n in names) if a]
    if not authors:
        return
    draft.authors = authors
    draft.title = match.group("title").strip().rstrip(",.")
    draft.year = int(years[-1])
    draft.venue = after.strip(" ,.") or None
    draft.parse_status = CitationParseStatus.parsed


# --- locating and segmenting the section -------------------------------------


def _flatten(nodes: list[SectionNode]) -> list[SectionNode]:
    out: list[SectionNode] = []
    for n in nodes:
        out.append(n)
        out.extend(_flatten(n.children))
    return out


def _reference_span(result: ExtractionResult, patterns: HeadingPatterns) -> list[TextBlock]:
    return select_references(result, patterns).span


def _reference_span_blocks(
    result: ExtractionResult, patterns: HeadingPatterns, issues: list[str]
) -> list[TextBlock]:
    """Blocks strictly between the references heading and the next heading
    of the same or higher rank (document order); [] when no reference
    section exists — that is a fact for F5 to report, not an error."""
    flat = _flatten(result.section_tree.nodes)
    ref_pos = next(
        (i for i, n in enumerate(flat) if patterns.is_reference_heading(n.title)),
        None,
    )
    blocks = [b for b in result.blocks if not b.is_furniture]
    if ref_pos is None:
        explicit = flat if result.section_tree.source in {"embedded_toc", "styles"} else []
        return _span_by_bare_heading(blocks, patterns, explicit, result.anchor_kind, issues)
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
    return _until_reference_end_heading(
        _after_heading(blocks, start, end, patterns, issues), patterns, blocks[start], issues
    )


def _after_heading(
    blocks: list[TextBlock], start: int, end: int, patterns: HeadingPatterns, issues: list[str]
) -> list[TextBlock]:
    """A heading and the first reference may occupy the same native text block."""
    heading = blocks[start]
    offset = 0
    for line in heading.text.splitlines(keepends=True):
        offset += len(line)
        if patterns.is_reference_heading(line):
            break
    else:
        if not patterns.is_reference_heading(heading.text):
            issues.append("ambiguous_reference_heading")
            return []
    tail = blocks[start + 1 : end]
    if heading.text[offset:].strip():
        return [slice_block(heading, offset, len(heading.text)), *tail]
    return tail


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


def _span_by_bare_heading(
    blocks: list[TextBlock],
    patterns: HeadingPatterns,
    explicit_nodes: list[SectionNode],
    anchor_kind: str,
    issues: list[str],
) -> list[TextBlock]:
    """Tree didn't include the reference section (e.g. source='none'):
    fall back to a standalone, unnumbered line naming it. A numbered line
    without a tree could be a TOC listing, so fail closed. Stop at a
    configured later heading rather than treating an appendix as references."""
    for i, b in enumerate(blocks):
        if patterns.is_reference_heading(b.text.splitlines()[0], allow_numbered=False):
            span = _until_reference_end_heading(
                _after_heading(blocks, i, len(blocks), patterns, issues), patterns, b, issues
            )
            return _until_explicit_heading(span, explicit_nodes, patterns, anchor_kind)
    return []


def _isolated_heading(block: TextBlock, node: SectionNode, patterns: HeadingPatterns) -> bool:
    if node.paragraph is not None:
        if block.paragraph != node.paragraph:
            return False
    elif node.page is None or block.page != node.page:
        return False
    text = normalize(block.text)
    if match_key(text) == match_key(node.title):
        return True
    parts = text.split(" ", 1)
    return (
        len(parts) == 2
        and patterns.standalone_heading_label.fullmatch(parts[0]) is not None
        and match_key(parts[1]) == match_key(node.title)
    )


def _until_explicit_heading(
    span: list[TextBlock],
    nodes: list[SectionNode],
    patterns: HeadingPatterns,
    anchor_kind: str,
) -> list[TextBlock]:
    """Use an omitted section's later outline/style boundary only when evidenced.

    Ambiguous locations, weak styling, continued reference lists and incomplete
    final entries retain their text. This is deliberately not a typography-only
    heading detector or a claim that every retained boundary is resolved.
    """
    candidates: dict[int, list[SectionNode]] = {}
    for node in nodes:
        matches = [i for i, block in enumerate(span) if _isolated_heading(block, node, patterns)]
        if len(matches) == 1:
            candidates.setdefault(matches[0], []).append(node)
    for index in sorted(candidates):
        if index == 0 or len(candidates[index]) != 1:
            continue
        block = span[index]
        preceding = span[:index]
        typical_bold = median(item.bold_ratio for item in preceding)
        typical_font = median(item.max_font_size for item in preceding)
        if not (
            (
                block.bold_ratio >= patterns.reference_end_min_bold_ratio
                and block.bold_ratio > typical_bold
            )
            or (
                typical_font > 0
                and block.max_font_size >= typical_font * patterns.reference_end_min_font_scale
            )
        ):
            continue
        level = candidates[index][0].level
        end = next(
            (
                later
                for later in sorted(candidates)
                if later > index and any(n.level <= level for n in candidates[later])
            ),
            len(span),
        )
        # A heading above more numbered/author-year entries is a bibliography
        # subsection, even if its numbering restarts or its style changes.
        tail = _split_numbered_block_lines(span[index + 1 : end])
        if any(
            _NUMBERED_ENTRY.match(item.text) is not None
            or _STANDALONE_REFERENCE_LABEL.fullmatch(item.text) is not None
            or parse_reference(item.text, 0).parse_status == CitationParseStatus.parsed
            for item in tail
        ):
            continue
        previous_groups = _segment_blocks(preceding, anchor_kind)
        if not previous_groups:
            continue
        last = parse_reference(_merge([item.text for item in previous_groups[-1]]), 0)
        if not (last.parse_status == CitationParseStatus.parsed or last.url or last.doi):
            continue
        return preceding
    return span


def _until_reference_end_heading(
    span: list[TextBlock],
    patterns: HeadingPatterns,
    reference_heading: TextBlock,
    issues: list[str],
) -> list[TextBlock]:
    for index, block in enumerate(span):
        # Match an entire original source line, not merely "Received" inside
        # a title. Native block packing must not change bibliography membership.
        offset = 0
        slicer = SourceSlicer(block)
        for line in block.text.splitlines(keepends=True):
            start, offset = offset, offset + len(line)
            history = any(p.fullmatch(normalize(line)) for p in patterns.reference_history_lines)
            end_heading = any(p.fullmatch(normalize(line)) for p in patterns.reference_end_headings)
            if not (history or end_heading):
                continue
            prefix = [slicer.slice(0, start)] if block.text[:start].strip() else []
            preceding = [*span[:index], *prefix]
            if history and not preceding:
                continue
            if end_heading:
                heading_block = slicer.slice(start, offset)
                if start and heading_block.source_lines is None:
                    issues.append("ambiguous_reference_end_style")
                    return span
                if not _contrasting_end_heading(
                    heading_block, preceding, reference_heading, patterns
                ):
                    continue
            tail = [slicer.slice(offset, len(block.text))] if block.text[offset:].strip() else []
            if _bibliography_continues([*tail, *span[index + 1 :]]):
                # A nested bibliography or an appendix containing references:
                # preserve source and do not turn its entries into body claims.
                issues.append("ambiguous_reference_end")
                return span
            return preceding
    return span


def _contrasting_end_heading(
    heading: TextBlock,
    preceding: list[TextBlock],
    reference_heading: TextBlock,
    patterns: HeadingPatterns,
) -> bool:
    if preceding:
        typical_bold = median(item.bold_ratio for item in preceding)
        typical_font = median(item.max_font_size for item in preceding)
        return (
            heading.bold_ratio >= patterns.reference_end_min_bold_ratio
            and heading.bold_ratio > typical_bold
        ) or (
            typical_font > 0
            and heading.max_font_size >= typical_font * patterns.reference_end_min_font_scale
        )
    return (
        heading.bold_ratio >= patterns.reference_end_min_bold_ratio
        and heading.bold_ratio >= reference_heading.bold_ratio
        and heading.max_font_size >= reference_heading.max_font_size
    ) or (
        reference_heading.max_font_size > 0
        and heading.max_font_size
        >= reference_heading.max_font_size * patterns.reference_end_min_font_scale
    )


def _bibliography_continues(span: list[TextBlock]) -> bool:
    return any(
        _NUMBERED_ENTRY.match(block.text) is not None
        or _STANDALONE_REFERENCE_LABEL.fullmatch(block.text) is not None
        or _AUTHOR_YEAR_ENTRY_START.match(block.text) is not None
        for block in _split_numbered_block_lines(span)
    )


def _segment(span: list[TextBlock], anchor_kind: str) -> list[str]:
    return [_merge([block.text for block in group]) for group in _segment_blocks(span, anchor_kind)]


def _segment_blocks(span: list[TextBlock], anchor_kind: str) -> list[list[TextBlock]]:
    if not span:
        return []
    if anchor_kind == "paragraph":
        return [[block] for block in span if block.text.strip()]

    # A PDF text block can contain many references. Inspect actual line
    # starts before applying the same consecutive-number boundary rule.
    # If it is not a numbered list, retain the original layout boundaries.
    first_style = next(
        (
            _number_marker_style(block.text)
            for block in span
            if _contextual_reference_number(block.text) is not None
        ),
        None,
    )
    # An original block must supply evidence of an actual printed list.
    # Consecutive wrapped years inside APA entries are not that evidence.
    numbered_span = _split_numbered_block_lines(span) if first_style is not None else span
    labels = [
        _contextual_reference_number(block.text)
        if _number_marker_style(block.text) == first_style
        else None
        for block in numbered_span
    ]
    numbered_labels = [label for label in labels if label is not None]
    if len(numbered_labels) != len(set(numbered_labels)):
        # Two appearances of the same line-start marker do not establish
        # which one is an entry and which might be quoted title text.
        # Preserve the original block boundaries rather than manufacture a
        # clean reference inventory from an ambiguous within-block split.
        numbered_span = span
        labels = [_contextual_reference_number(block.text) for block in numbered_span]
    first_label = next((number for number in labels if number is not None), None)
    # A wrapped publication year such as "2019. Journal" cannot restart
    # a bracketed bibliography, even when 2020 appears further down.
    labels = [
        label if _number_marker_style(block.text) == first_style else None
        for block, label in zip(numbered_span, labels, strict=True)
    ]
    if first_label is not None and first_label + 1 in labels:
        return _segment_numbered(numbered_span, labels, first_label)

    # Page anchors: hanging indent. The section's leftmost x0 is where
    # entries start; anything to the right continues the previous entry.
    xs = [b.bbox[0] for b in span if b.bbox is not None]
    entry_x = min(xs) if xs else 0.0
    entries: list[list[TextBlock]] = []
    current: list[TextBlock] = []
    for b in span:
        x0 = b.bbox[0] if b.bbox is not None else entry_x
        # 3pt: rendering jitter on identical indents, far below any real
        # hanging indent (~0.25in = 18pt) — layout invariant, not a knob.
        if x0 <= entry_x + 3.0 and current:
            entries.append(current)
            current = []
        current.append(b)
    if current:
        entries.append(current)
    return [group for group in entries if _merge([block.text for block in group])]


def _split_numbered_block_lines(span: list[TextBlock]) -> list[TextBlock]:
    """Source-faithful candidates, not yet accepted reference boundaries.

    Only real line starts are considered; an inline citation in a title is
    not a list marker. A marker on its own line may use the following line
    for its entry text. Sliced blocks keep the original page but lose the
    multi-entry rectangle: it cannot honestly identify just one reference.
    """
    fragments: list[TextBlock] = []
    for block in span:
        lines = block.text.splitlines(keepends=True)
        starts = [0]
        offset = 0
        for index, line in enumerate(lines):
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if offset and _contextual_reference_number(line + following) is not None:
                starts.append(offset)
            offset += len(line)
        if len(starts) == 1:
            fragments.append(block)
            continue
        slicer = SourceSlicer(block)
        for start, end in zip(starts, [*starts[1:], len(block.text)], strict=True):
            fragment = block.text[start:end]
            if fragment.strip():
                fragments.append(slicer.slice(start, end))
    return fragments


def _leading_reference_number(text: str) -> int | None:
    """A printed list marker followed by an alphabetic entry start.

    Numeric DOI tails (`10. 1300/...`), years and page ranges are not
    bibliography boundaries just because they start with digits.
    """
    match = _NUMBERED_ENTRY.match(text)
    if match is None:
        return None
    entry_start = (match.group(3) or match.group(4)).lstrip(_ENTRY_OPENING_QUOTES)
    entry_start = _ENTRY_AUTHOR_REPEAT.sub("", entry_start).lstrip(_ENTRY_OPENING_QUOTES)
    if not entry_start[:1].isalpha():
        return None
    return _parse_reference_number(match.group(1) or match.group(2))


def _contextual_reference_number(text: str) -> int | None:
    """Bracketed numeric-first entries may join an established consecutive list.

    This only proposes a marker. The caller must still require a same-style
    consecutive sequence. Dotted DOI/year fragments retain the stronger guard.
    """
    strong = _leading_reference_number(text)
    if strong is not None:
        return strong
    match = _NUMBERED_ENTRY.match(text)
    if match is not None and match.group(2) and (match.group(3) or "").strip():
        return _parse_reference_number(match.group(2))
    return None


def _parse_reference_number(text: str) -> int | None:
    """Honor the runtime's integer safety limit without failing ingestion."""
    try:
        return int(text)
    except ValueError:
        return None


def _number_marker_style(text: str) -> str | None:
    match = _NUMBERED_ENTRY.match(text)
    if match is None:
        return None
    return "[]" if match.group(2) else text.lstrip()[len(match.group(1))]


def _segment_numbered(
    span: list[TextBlock], labels: list[int | None], first_label: int
) -> list[list[TextBlock]]:
    entries: list[list[TextBlock]] = []
    current: list[TextBlock] = []
    expected = first_label
    for index, (block, label) in enumerate(zip(span, labels, strict=True)):
        # A genuinely missing printed number should not merge the rest of
        # the bibliography. Require a following consecutive label before
        # accepting a jump; a lone year/volume at the start of a wrapped
        # line is then kept with its current reference.
        following_pair = (
            label is not None
            and label > expected
            and expected not in labels[index + 1 :]
            and label + 1 in labels[index + 1 :]
        )
        if label == expected or following_pair:
            if current:
                entries.append(current)
            current = []
            expected = label + 1
        current.append(block)
    if current:
        entries.append(current)
    return [group for group in entries if _merge([block.text for block in group])]


def _merge(parts: list[str]) -> str:
    return _join_lines("\n".join(parts))


def _join_lines(text: str) -> str:
    """Preserve word boundaries. URL recovery separately uses original evidence."""
    return normalize(text)
