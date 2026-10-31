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
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from statistics import median

from app.config import get_settings
from app.ingest.citation_dates import date_patterns
from app.ingest.normalize import match_key, normalize
from app.ingest.patterns import HeadingPatterns
from app.ingest.reference_dois import recover_reference_dois
from app.ingest.reference_metadata_patterns import load_reference_metadata_patterns
from app.ingest.reference_urls import reference_urls, text_url
from app.ingest.schemas import Bbox, ExtractionResult, SectionNode, TextBlock
from app.ingest.source_slices import SourceFragment, SourceSlicer, line_inventory_valid, slice_block
from app.models.enums import CitationParseStatus

# APA-shape invariants. YEAR is the entry's backbone: "(2024)." with
# optional letter suffix ("2024a") or month/day detail ("2025, June 28").
_YEAR = re.compile(r"\((\d{4}|n\.?\s*d\.)[a-z]?(?:,[^)]*)?\)", re.IGNORECASE)
_AUTHOR_YEAR_ENTRY_START = re.compile(
    r"^[^()\n]+\((\d{4}|n\.?\s*d\.)[a-z]?(?:,[^)]*)?\)\.\s*\S", re.IGNORECASE
)
# Numeric publication metadata is not an APA date, even if the issue has
# four digits that could be a year. Do not invent a date for an unsupported
# reference style; keep the original entry available for verification.
_VOLUME_ISSUE_PAGES = re.compile(r"\b\d+\s*\((?P<issue>\d+)\)\s*[:,]\s*\d+")
_DOI = re.compile(r"\b10\.\d{4,9}/[^\s]+")
_ISBN = re.compile(r"\bISBN[:\s-]*((?:[\dXx][- ]?){10,17})", re.IGNORECASE)
_URL = re.compile(r"https?://\S+")
# Selection issues that leave the bibliography's place in the document unknown,
# as opposed to issues about its entries or where it ends.
_REFERENCE_LOCATION_UNKNOWN = frozenset(
    {"ambiguous_reference_heading", "invalid_source_line_inventory"}
)
REFERENCE_LOCATION_UNKNOWN = _REFERENCE_LOCATION_UNKNOWN
# Selection issues that leave the bibliography's end unknown. A later pass can
# still shorten such a span, so text-only callers leave out everything from the
# bibliography onward instead of trusting where the span stops.
_REFERENCE_END_UNCLEAR = frozenset({"ambiguous_reference_end", "ambiguous_reference_end_style"})
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
# Rendering jitter between lines set at the same indent: far below any real
# list indent (~0.25in = 18pt). A layout invariant, not a tuning knob.
_INDENT_JITTER_PTS = 3.0
# Set only while checking citation rows saved by the reader before reference
# metadata v4, so a re-run compares like with like (see previous_reference_reader).
_PREVIOUS_READER: ContextVar[bool] = ContextVar("previous_reference_reader", default=False)


def possible_split_doi(raw_text: str, extracted_doi: str) -> bool:
    """Whether a stored DOI may stop before a whitespace-separated tail.

    DOI names may legitimately contain spaces. Once source layout is
    flattened, a following word, number, or punctuation-led tail may be
    ordinary reference metadata OR part of the DOI; do not turn a negative
    prefix lookup into a finding.
    This deliberately trades coverage for precision and never reconstructs an
    identifier. A terminal DOI remains eligible for normal verification.
    """
    match = re.search(
        rf"(?<!\w){re.escape(extracted_doi)}[.,;:)]*\s+(?=\S)",
        raw_text,
    )
    if match is None:
        return False
    # An explicitly separate locator is not a continuation of the prior DOI.
    following = raw_text[match.end() :]
    patterns = load_reference_metadata_patterns(get_settings().reference_metadata_patterns_file)
    if patterns.locator_label is not None and patterns.locator_label.match(following):
        return False
    return not re.match(r"https?://|(?:doi:?\s*)?10\.\d{4,9}/", following, re.I)


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
    heading: TextBlock | None = None
    protected_span: list[TextBlock] = field(default_factory=list)

    @property
    def membership_issues(self) -> tuple[str, ...]:
        return ("reference_tail_membership_unresolved",) if self.protected_span else ()


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
    headings: list[TextBlock] = []
    protected: list[TextBlock] = []
    span = _reference_span_blocks(
        result.model_copy(update={"blocks": projected}), patterns, issues, headings, protected
    )
    for block in span:
        for line in block.text.splitlines():
            marker = _REFERENCE_MARKER.match(line)
            if marker and _parse_reference_number(marker.group(1) or marker.group(2)) is None:
                return ReferenceSelection([], span, (*issues, "unreadable_reference_number"))
    if (
        result.anchor_kind == "page"
        and _first_line_indent_evidence(
            span, min((b.bbox[0] for b in span if b.bbox is not None), default=0.0)
        )[1]
    ):
        issues.append("ambiguous_reference_entry_boundaries")
    groups = _entry_groups(span, result.anchor_kind)
    if groups is None:
        # An entry that is only its label means the labels were read apart
        # from the entries (a labels column beside a text column) and could
        # not be rebuilt from line positions, so none of them is trusted.
        groups = _segment_blocks(span, result.anchor_kind)
        issues.append("detached_reference_labels")
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
    return ReferenceSelection(
        groups, span, tuple(issues), headings[0] if len(headings) == 1 else None, protected
    )


@contextmanager
def previous_reference_reader():
    """Read the reference list as the reader before reference metadata v4 did.

    Citation rows are saved at upload. A manuscript uploaded before v4 keeps
    the reading its rows were saved with when it is checked again, instead of
    failing the row comparison because the reader improved. v4 added: export
    markup read through, first-line-indent lists, list labels read before
    their heading, complete-entry evidence before an unclassified appendix,
    titles stopped at the printed locator or a comma after the date, and
    authors closed by a period before a quoted title. New heading names are
    data and are not undone; a list they newly find had no saved rows.
    """
    token = _PREVIOUS_READER.set(True)
    try:
        yield
    finally:
        _PREVIOUS_READER.reset(token)


def extract_references(
    result: ExtractionResult, patterns: HeadingPatterns, *, _legacy_quoted_titles: bool = False
) -> list[CitationDraft]:
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
    settings = get_settings()
    metadata_patterns = load_reference_metadata_patterns(settings.reference_metadata_patterns_file)
    recoveries = recover_reference_dois(
        result,
        groups,
        resolver_hosts=metadata_patterns.doi_resolver_hosts,
        tolerance=patterns.reference_url_geometry_tolerance_pts,
        max_chars=settings.reference_metadata_max_chars,
        max_comparisons=settings.reference_doi_max_geometry_comparisons,
    )
    drafts = []
    for index, (group, recovery) in enumerate(zip(groups, recoveries, strict=True)):
        raw = "\n".join(block.text for block in group)
        parser_view = (
            raw[: recovery.start] + recovery.doi + raw[recovery.end :]
            if recovery is not None
            else raw
        )
        legacy = _PREVIOUS_READER.get()
        shown = parser_view if legacy else _read_through_markup(parser_view, metadata_patterns)
        draft = parse_reference(
            _join_lines(shown), index, _legacy_quoted_titles=_legacy_quoted_titles
        )
        if recovery is not None:
            # The source/link contract establishes the complete identifier.
            # Generic reference punctuation stripping must not shorten it.
            draft.doi = recovery.doi
        if not legacy and urls[index] and "](" in urls[index]:
            urls[index] = _before_markup_link(urls[index])
        if not legacy and metadata_patterns.markup_link.search(parser_view):
            # A link annotation behind printed link markup points at the
            # target, which may be a search redirect. Only the address the
            # entry shows is its locator.
            url = text_url(shown)
            urls[index] = _before_markup_link(url) if url else None
        draft.raw_text = _join_lines(raw)
        drafts.append(draft)
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
                # A label printed apart from its entry is its own first block.
                printed_number=_contextual_reference_number(
                    _merge([block.text for block in group])
                ),
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
    heading = selection.heading
    # Labels read before the heading on its own page: the heading, which
    # follows them, is the list's opening boundary, not the text before them.
    labels_first = (
        isinstance(heading, SourceFragment)
        and heading.source_block_index > start
        and heading.page == first.page
    )
    # A split proves a local boundary only when the excluded source contains
    # non-furniture text. Removing a page number or blank line proves nothing
    # about where the bibliography starts or finishes.
    if not labels_first and not _has_body_text(result.blocks[start], 0, first.source_start):
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
    exclude_heading: bool = False,
    ambiguous_entries_ok: bool = False,
) -> list[TextBlock]:
    """Exact complement of bibliography/furniture intervals, not object identity.

    A caller that only needs text known to lie outside the bibliography may
    pass `ambiguous_entries_ok`. Then a bibliography whose entries are
    ambiguous is left out as its whole region, and one whose end is unclear is
    left out together with everything after it, instead of the result being
    empty. `reference_region_not_assessed` names that text. An unknown
    bibliography location still returns nothing.
    """
    selection = selection or select_references(result, patterns)
    if selection.issues and not (
        ambiguous_entries_ok
        and selection.span
        and not set(selection.issues) & _REFERENCE_LOCATION_UNKNOWN
    ):
        return []
    excluded: dict[int, list[tuple[int, int]]] = {}
    intervals = [*selection.span, *selection.protected_span]
    if exclude_heading and isinstance(selection.heading, SourceFragment):
        intervals.append(selection.heading)
    for fragment in intervals:
        excluded.setdefault(fragment.source_block_index, []).append(
            (fragment.source_start, fragment.source_end)
        )
    cut = _unclear_end_cut(selection)
    body = []
    for index, block in enumerate(result.blocks):
        if block.is_furniture or not block.text:
            continue
        if cut is not None and index > cut[0]:
            continue
        intervals = list(excluded.get(index, []))
        if cut is not None and index == cut[0]:
            intervals.append((cut[1], len(block.text)))
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


def _unclear_end_cut(selection: ReferenceSelection) -> tuple[int, int] | None:
    """Where the bibliography starts, when its end is unknown."""
    if not set(selection.issues) & _REFERENCE_END_UNCLEAR or not selection.span:
        return None
    first = min(
        selection.span,
        key=lambda fragment: (fragment.source_block_index, fragment.source_start),
    )
    return first.source_block_index, first.source_start


@dataclass(frozen=True)
class RegionNotAssessed:
    """Text that text-only callers leave out because the bibliography's end
    is unclear: from where the reference list starts to the end of the
    document."""

    # Where that text starts, as reports cite it ("p. 157", "¶212"); None if
    # the source block carries no anchor.
    start: str | None
    # PDF pages from there to the end that hold text; empty for Word files.
    pages: list[int]


def reference_region_not_assessed(
    result: ExtractionResult, selection: ReferenceSelection
) -> RegionNotAssessed | None:
    """None when the bibliography's end is known or no list was found."""
    cut = _unclear_end_cut(selection)
    if cut is None:
        return None
    first = result.blocks[cut[0]]
    if result.anchor_kind != "page":
        return RegionNotAssessed(
            start=f"¶{first.paragraph}" if first.paragraph is not None else None, pages=[]
        )
    return RegionNotAssessed(
        start=f"p. {first.page}" if first.page is not None else None,
        pages=sorted(
            {
                block.page
                for block in result.blocks[cut[0] :]
                if block.page is not None and not block.is_furniture and block.text.strip()
            }
        ),
    )


def reference_section_page(result: ExtractionResult, patterns: HeadingPatterns) -> int | None:
    """First evidenced reference-list page for a manuscript deep link.

    A missing page anchor stays absent; callers must not guess a bibliography
    location from reference order or a fixed page number.
    """
    selection = select_references(result, patterns)
    return next((block.page for block in selection.span if block.page is not None), None)


def parse_reference(
    raw: str, order_index: int, *, _legacy_quoted_titles: bool = False
) -> CitationDraft:
    """Best-effort APA parse. Never raises: any surprise leaves the entry
    as parse_failed with its raw text intact."""
    draft = CitationDraft(order_index=order_index, raw_text=raw)
    # Bound structured inference, not the retained source. Oversized entries
    # remain available verbatim and cannot become verified metadata.
    if len(raw) > get_settings().reference_metadata_max_chars:
        return draft
    try:
        _parse_into(draft, raw, legacy_quoted_titles=_legacy_quoted_titles)
        if draft.doi and possible_split_doi(raw, draft.doi):
            # Raw source survives, but an identifier prefix cannot establish
            # either a successful or a negative registry lookup.
            draft.doi = None
            draft.parse_status = CitationParseStatus.parse_failed
    except Exception:  # noqa: BLE001 — the QA property "never throws" outranks
        draft.parse_status = CitationParseStatus.parse_failed
    return draft


@dataclass(frozen=True)
class QuotedTitleSpan:
    start: int
    end: int
    after: int


def quoted_title_span(raw: str, start: int) -> QuotedTitleSpan | None:
    """An exact title span only at an independently established title start.

    Leading quotation punctuation is a commitment to this grammar. A broken
    pair cannot fall through to an unquoted whole-remainder title.
    """
    patterns = load_reference_metadata_patterns(get_settings().reference_metadata_patterns_file)
    while start < len(raw) and (
        raw[start].isspace() or raw[start] in patterns.quoted_title_separators
    ):
        start += 1
    pairs = dict(patterns.quoted_title_pairs)
    delimiters = {char for pair in patterns.quoted_title_pairs for char in pair}
    if start == len(raw) or raw[start] not in delimiters:
        return None
    if raw[start] not in pairs:
        raise ValueError("unmatched_quoted_title_opener")
    close = next((i for i in range(start + 1, len(raw)) if raw[i] in delimiters), None)
    if close is None or raw[close] != pairs[raw[start]]:
        raise ValueError("unmatched_quoted_title_pair")
    after = close + 1
    if any(char in delimiters for char in raw[after:]):
        raise ValueError("ambiguous_extra_quoted_fields")
    if after < len(raw) and not (
        raw[after].isspace() or raw[after] in patterns.quoted_title_separators
    ):
        raise ValueError("ambiguous_quoted_title_boundary")
    # A second leading quote would turn the apparent tail into another title.
    tail_start = after
    while tail_start < len(raw) and (
        raw[tail_start].isspace() or raw[tail_start] in patterns.quoted_title_separators
    ):
        tail_start += 1
    if tail_start < len(raw) and raw[tail_start] in delimiters:
        raise ValueError("ambiguous_nested_quoted_title")
    title_start, title_end = start + 1, close
    while title_start < title_end and raw[title_start].isspace():
        title_start += 1
    while title_end > title_start and (
        raw[title_end - 1].isspace()
        or raw[title_end - 1] in patterns.quoted_title_terminal_delimiters
    ):
        title_end -= 1
    if not any(char.isalnum() for char in raw[title_start:title_end]):
        raise ValueError("empty_quoted_title")
    return QuotedTitleSpan(title_start, title_end, after)


def apa_quoted_title_span(raw: str) -> QuotedTitleSpan | None:
    """The APA date/title boundary used by the versioned run-local repair."""
    issue_spans = {match.span("issue") for match in _VOLUME_ISSUE_PAGES.finditer(raw)}
    date = next(
        (m for m in date_patterns()["reference"].finditer(raw) if m.span(1) not in issue_spans),
        None,
    )
    return quoted_title_span(raw, date.end()) if date else None


def _parse_locators(draft: CitationDraft, raw: str) -> None:
    draft.doi, draft.isbn = None, None
    doi = _DOI.search(raw)
    legacy = _PREVIOUS_READER.get()
    if doi:
        value = doi.group(0) if legacy else _before_markup_link(doi.group(0))
        draft.doi = value.rstrip(_TRAILING_PUNCT)
    isbn = _ISBN.search(raw)
    if isbn:
        draft.isbn = re.sub(r"[- ]", "", isbn.group(1)).upper()
    url = text_url(raw)
    draft.url = _before_markup_link(url) if url and not legacy else url


def _read_through_markup(text: str, patterns) -> str:
    """The entry as its reader sees it, without the link and emphasis markup
    some document exports print literally ("[shown](target)", "*Journal*").

    Line breaks are kept, so an address wrapped across lines still reads as
    wrapped and is never joined by guesswork. A link whose shown text is
    already an address keeps only that text. Otherwise the target follows the
    shown text, unless the target is a redirect that wraps another address.
    """

    def link(match: re.Match[str]) -> str:
        shown, target = match.group("shown"), match.group("target")
        if patterns.markup_shown_locator.match(shown) or patterns.markup_redirect.search(target):
            return shown
        return f"{shown} {target}"

    text = patterns.markup_link.sub(link, text)
    return patterns.markup_emphasis.sub(lambda match: match.group("shown"), text)


def _before_markup_link(identifier: str) -> str:
    """An identifier read from link markup the reader could not pair ends at
    "](": what follows is the link target, possibly a search redirect."""
    return identifier.split("](", 1)[0]


def _parse_group(texts: list[str], order_index: int = 0) -> CitationDraft:
    """Parse an entry exactly as ingestion stores it, so a boundary decision
    never rests on a reading that differs from the saved one."""
    return parse_reference(_shown_entry("\n".join(texts)), order_index)


def _shown_entry(raw: str) -> str:
    """The parse view of an entry. With an unreadable metadata policy the raw
    text is returned, and parsing then fails closed on the same policy."""
    if _PREVIOUS_READER.get():
        return _join_lines(raw)
    try:
        patterns = load_reference_metadata_patterns(get_settings().reference_metadata_patterns_file)
    except (OSError, ValueError, UnicodeError):
        return _join_lines(raw)
    return _join_lines(_read_through_markup(raw, patterns))


def _parse_into(draft: CitationDraft, raw: str, *, legacy_quoted_titles: bool = False) -> None:
    _parse_locators(draft, raw)
    issue_spans = {match.span("issue") for match in _VOLUME_ISSUE_PAGES.finditer(raw)}
    year_m = next(
        (
            match
            for match in date_patterns()["reference"].finditer(raw)
            if match.span(1) not in issue_spans
        ),
        None,
    )
    metadata_patterns = load_reference_metadata_patterns(
        get_settings().reference_metadata_patterns_file
    )
    quote_chars = {char for pair in metadata_patterns.quoted_title_pairs for char in pair}
    first_quote = next((i for i, char in enumerate(raw) if char in quote_chars), None)
    if not year_m or (
        not legacy_quoted_titles and first_quote is not None and first_quote < year_m.start()
    ):
        # IEEE-style numbered entries print the year at the end, not in
        # parentheses; their title is the quoted span.
        _parse_ieee_into(draft, raw, legacy_quoted_titles=legacy_quoted_titles)
        if draft.parse_status != CitationParseStatus.parsed and first_quote is None:
            _parse_unquoted_numbered_into(draft, raw)
        return  # otherwise no year backbone -> parse_failed, raw preserved
    raw_year = year_m.group(1).strip()
    if raw_year[:4].isdigit():
        draft.year = int(raw_year[:4])
    else:
        # Preserve explicit no-date syntax in raw_text; the numeric year is
        # unknown. reference_date_key recovers the source's date identity.
        draft.year = None

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

    if not legacy_quoted_titles and (span := quoted_title_span(raw, year_m.end())):
        _parse_locators(draft, raw[: year_m.end()] + raw[span.after :])
        draft.title = raw[span.start : span.end]
        tail = raw[span.after :]
        draft.venue = _RETRIEVED.sub("", _DOI_TAIL.sub("", _URL.sub("", tail))).strip(" ,.") or None
        if draft.authors:
            draft.parse_status = CitationParseStatus.parsed
        return

    previous = _PREVIOUS_READER.get()
    # "(2021), Title" puts a comma where APA prints a period.
    rest = raw[year_m.end() :].lstrip(". " if previous else ".,; ").strip()
    locator = min(
        [*_URL.finditer(rest), *_DOI_TAIL.finditer(rest)],
        key=lambda match: match.start(),
        default=None,
    )
    if locator is not None and not previous:
        # An APA entry prints its locator last. Text after it is the rest of an
        # address wrapped across lines or a note, never the title or venue.
        rest = rest[: locator.start()].strip()
    # The URL/DOI tail is locator, not venue text: "https://doi.org/10...",
    # "doi:10..." and a bare "10.xxxx/..." alike, plus "Retrieved from".
    rest = _URL.sub("", rest)
    rest = _DOI_TAIL.sub("", rest)
    rest = _RETRIEVED.sub("", rest).strip()
    title, venue = _split_title_venue(rest)
    if venue is None and not previous and draft.authors and _title_before_date(draft.authors):
        # APA moves the title of a work with no author in front of the date
        # ("Title of the page. (n.d.). Site name."). The one part after the
        # date is then the site, not the title, so neither is read as one;
        # the entry stays for the instructor to check.
        draft.authors = None
        return
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


def _title_before_date(authors: list[str]) -> bool:
    """Whether the text before the date reads as a title, not as authors: no
    name carries an initial, none is a corporate author, and one runs longer
    than a personal name with lowercase words in it, as titles do
    ("Analysis of recommender systems"). A company name in capitals
    ("Example Infotech Services Limited") stays an author."""
    settings = get_settings()
    patterns = load_reference_metadata_patterns(settings.reference_metadata_patterns_file)
    for author in authors:
        words = author.replace(",", " ").split()
        if any(_INITIALS.fullmatch(word) for word in words):
            return False
        institution = patterns.corporate_author_words | patterns.author_institution_words
        if any(word.strip(".,()").casefold() in institution for word in words):
            return False
    return any(
        len(words) > settings.reference_unseparated_author_max_tokens
        and any(
            word[:1].islower() and word.casefold() not in patterns.name_particles for word in words
        )
        for words in (author.split() for author in authors)
    )


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


def _ieee_author_names(authors_part: str) -> list[str]:
    return [
        name.strip()
        for name in re.split(
            r",\s*(?:and\s+)?|\s+and\s+", re.sub(r"\bet\s+al\.?", "", authors_part)
        )
        if name.strip()
    ]


def ieee_quoted_author_prefix_supported(prefix: str) -> bool:
    """Linear token scan for the same explicit author shape used at ingestion."""
    body = _LEADING_LABEL.sub("", prefix.strip(), count=1)
    if not body.endswith(",") or any(char in body for char in "()"):
        return False
    # Bound the splitter to single separators without changing saved text or
    # source offsets. Adjacent whitespace must not create repeated searches.
    names = _ieee_author_names(" ".join(body[:-1].split()))
    for name in names:
        words = name.split()
        if len(words) < 2 or not _INITIALS.fullmatch(words[0]):
            return False
    return bool(names)


def _parse_ieee_into(draft: CitationDraft, raw: str, *, legacy_quoted_titles: bool = False) -> None:
    body = _LEADING_LABEL.sub("", raw, count=1)
    if legacy_quoted_titles:
        match = _IEEE_QUOTED_TITLE.search(body)
        if match is None:
            return
        authors_part = body[: match.start()].strip().rstrip(",").strip()
        after = _DOI_TAIL.sub("", _URL.sub("", body[match.end() :]))
        title = match.group("title").strip().rstrip(",.")
        years = _IEEE_YEAR.findall(after)
    else:
        patterns = load_reference_metadata_patterns(get_settings().reference_metadata_patterns_file)
        quote_chars = {char for pair in patterns.quoted_title_pairs for char in pair}
        start = next((i for i, char in enumerate(body) if char in quote_chars), None)
        # Some styles close the author list with a period instead of a comma
        # ("A. Cruz and B. Reyes. "Title.""). Each name must still begin with
        # its initials, which is checked below before any field is kept.
        separators = "," if _PREVIOUS_READER.get() else ",."
        if start is None or body[:start].rstrip()[-1:] not in tuple(separators):
            return
        authors_part = body[:start].strip().rstrip(separators).strip()
        span = quoted_title_span(body, start)
        if span is None:
            return
        title = body[span.start : span.end]
        _parse_locators(draft, body[:start] + body[span.after :])
        after = body[span.after :]
        access = patterns.access_note.search(after)
        if access:
            after = after[: access.start()]
        after = _DOI_TAIL.sub("", _URL.sub("", after))
        years = list({m.group("year") for m in patterns.publication_year.finditer(after)})
        if len(years) != 1:
            return
    if not authors_part or not years:
        return
    names = _ieee_author_names(authors_part)
    authors = [a for a in (_ieee_author(n) for n in names) if a]
    if not legacy_quoted_titles and any(
        not name.split() or not _INITIALS.fullmatch(name.split()[0]) or len(name.split()) < 2
        for name in names
    ):
        return  # no independent initials/surname boundary before the title
    if (
        not legacy_quoted_titles
        and not _PREVIOUS_READER.get()
        and any(
            word.endswith(".") and word[:1].isupper() and not _INITIALS.fullmatch(word)
            for name in names
            for word in name.split()
        )
    ):
        return  # a sentence ends inside what would be the author list
    if not authors:
        return
    draft.authors = authors
    draft.title = title
    draft.year = int(years[-1])
    draft.venue = after.strip(" ,.") or None
    draft.parse_status = CitationParseStatus.parsed


def _parse_unquoted_numbered_into(draft: CitationDraft, raw: str) -> None:
    """Recognize author-period-title/publication-tail references, not free prose.

    Access dates and locator digits are not publication years. Full natural
    names remain as printed; no family-name order or absent author delimiter is
    invented. This is metadata extraction, never source verification.
    """
    if not _LEADING_LABEL.match(raw):
        return
    body = _LEADING_LABEL.sub("", raw, count=1)
    settings = get_settings()
    patterns = load_reference_metadata_patterns(settings.reference_metadata_patterns_file)
    # Only a date-qualified or explicitly delimited access note is removed,
    # not an access-related word that happens to occur inside a work's title.
    access = patterns.access_note.search(body)
    publication = body[: access.start()] if access else body
    boundary = next(
        (
            match
            for match in re.finditer(r"\.\s+", publication)
            if not _INITIALS.fullmatch(publication[: match.start() + 1].split()[-1])
        ),
        None,
    )
    if boundary is None:
        return
    author_text = publication[: boundary.start()].strip()
    remainder = publication[boundary.end() :].strip()
    # Establish the title boundary before interpreting dates. A date printed
    # inside the title is source text, not evidence of publication year.
    title, tail = _split_title_venue(remainder)
    locator = min(
        [*_URL.finditer(remainder), *_DOI_TAIL.finditer(remainder)],
        key=lambda match: match.start(),
        default=None,
    )
    if locator and (tail is None or locator.start() < len(title)):
        before_locator = remainder[: locator.start()].rstrip()
        if not before_locator.endswith("."):
            return
        title, tail = before_locator[:-1].strip(), remainder[locator.start() :]
    if tail is None:
        return  # no independent title/publication boundary
    if not title or not any(character.isalpha() for character in title):
        return
    # A whitespace-split locator is not reconstructed by guessing. Its trailing
    # path can contain dates or punctuation; keep the raw entry for correction.
    if re.search(r"https?://\s|\b10\.\d{4,9}/\s", tail):
        return
    # A separately labelled next URL is not a continuation of the preceding
    # DOI. Remove only a configured label directly followed by a URL start,
    # inside the already-established publication tail, never in source text.
    if patterns.locator_label is not None:
        tail = patterns.locator_label.sub("", tail)
    for locator in [*_URL.finditer(tail), *_DOI_TAIL.finditer(tail)]:
        continuation = tail[locator.end() :].lstrip()
        if not continuation:
            continue
        following = continuation.lstrip(".,; ")
        if _URL.match(following) or _DOI_TAIL.match(following):
            continue  # separately printed next locator
        if continuation[0] not in ".,;" and locator.group(0)[-1] not in ".,;":
            return
        # Terminal punctuation on the first fragment is not enough: it can be
        # a split domain/path. Only a standalone date tail is supported here.
        date_tail = f", {following}"
        date = patterns.publication_year.match(date_tail)
        if date is None or date_tail[date.end() :].strip(".,; "):
            return

    # Keep following punctuation: the comma outside a URL can introduce an
    # independently printed publication year. Never remove dates from title.
    def remove_locator(match: re.Match[str]) -> str:
        value = match.group(0)
        return value[len(value.rstrip(".,;")) :]

    tail = _DOI_TAIL.sub(remove_locator, _URL.sub(remove_locator, tail)).strip()
    dates = list(patterns.publication_year.finditer(tail))
    years = {int(match.group("year")) for match in dates}
    if len(years) != 1:
        return
    venue = patterns.publication_year.sub("", tail).strip(" ,.;") or None
    # A numeric label alone is not a title, nor is locator/publication metadata
    # after an undivided author/title phrase. Author punctuation needs its own
    # evidence even when the year and title can be retained as partial fields.
    if not author_text or any(character.isdigit() for character in author_text):
        return
    draft.title, draft.year, draft.venue = title, next(iter(years)), venue
    corporate = any(
        word.strip(".,()").casefold() in patterns.corporate_author_words
        for word in author_text.split()
    )
    names = (
        [author_text]
        if corporate
        else [
            name.strip()
            for name in re.split(r",\s*(?:and\s+)?|\s+(?:and|&)\s+", author_text)
            if name.strip()
        ]
    )
    authors = []
    for name in names:
        words = name.split()
        substantive = [
            word
            for word in words
            if not _INITIALS.fullmatch(word) and word.casefold() not in patterns.name_particles
        ]
        if not corporate and (
            not substantive
            or len(substantive) > settings.reference_unseparated_author_max_tokens
            or any(
                not re.fullmatch(r"[^\W\d_][\w'\u2019.\-]*", word)
                or (not word[0].isupper() and word.casefold() not in patterns.name_particles)
                for word in words
            )
        ):
            return
        author = _ieee_author(name)
        if author is None:
            return
        authors.append(author)
    if authors:
        draft.authors = authors
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
    result: ExtractionResult,
    patterns: HeadingPatterns,
    issues: list[str],
    headings: list[TextBlock] | None = None,
    protected: list[TextBlock] | None = None,
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
        return _span_by_bare_heading(
            blocks, patterns, explicit, result.anchor_kind, issues, headings, protected
        )
    node = flat[ref_pos]
    successor = next((n for n in flat[ref_pos + 1 :] if n.level <= node.level), None)

    start = _block_index_of_heading(blocks, node)
    if start is None:
        return []
    if (
        successor is not None
        and parse_reference(successor.title, 0).parse_status == CitationParseStatus.parsed
    ):
        successor = None
    return _until_reference_end_heading(
        _after_heading(blocks, start, len(blocks), patterns, issues, headings=headings),
        patterns,
        blocks[start],
        issues,
        protected=protected,
        anchor_kind=result.anchor_kind,
        outline_end=successor,
    )


def _after_heading(
    blocks: list[TextBlock],
    start: int,
    end: int,
    patterns: HeadingPatterns,
    issues: list[str],
    *,
    allow_numbered: bool = True,
    headings: list[TextBlock] | None = None,
) -> list[TextBlock]:
    """A heading and the first reference may occupy the same native text block."""
    heading = blocks[start]
    offset = 0
    for line in heading.text.splitlines(keepends=True):
        line_start = offset
        offset += len(line)
        if patterns.is_reference_heading(line, allow_numbered=allow_numbered):
            if headings is not None:
                headings.append(slice_block(heading, line_start, offset))
            break
    else:
        if not patterns.is_reference_heading(heading.text, allow_numbered=allow_numbered):
            issues.append("ambiguous_reference_heading")
            return []
        if headings is not None:
            headings.append(heading)
    tail = blocks[start + 1 : end]
    if heading.text[offset:].strip():
        tail = [slice_block(heading, offset, len(heading.text)), *tail]
    return [*_labels_read_before(blocks, start), *tail]


def _labels_read_before(blocks: list[TextBlock], start: int) -> list[TextBlock]:
    """List labels printed below the heading but read before it.

    A numbered list set as a labels column beside a text column can be read
    labels first, so the first page's "[1]".."[11]" come before the heading.
    Only an unbroken run of label-only blocks directly before the heading, on
    its page and lower on that page than the heading, is taken back.
    """
    heading = blocks[start]
    if _PREVIOUS_READER.get() or heading.page is None or heading.bbox is None:
        return []
    run: list[TextBlock] = []
    for block in reversed(blocks[:start]):
        if not (
            _standalone_label(block.text)
            and block.page == heading.page
            and block.bbox is not None
            and block.bbox[1] >= heading.bbox[3]
        ):
            break
        run.append(block)
    return run[::-1]


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
    headings: list[TextBlock] | None = None,
    protected: list[TextBlock] | None = None,
) -> list[TextBlock]:
    """Tree didn't include the reference section (e.g. source='none'):
    fall back to a standalone, unnumbered line naming it. A numbered line
    without a tree could be a TOC listing, so fail closed. Stop at a
    configured later heading rather than treating an appendix as references."""
    for i, b in enumerate(blocks):
        # Source-line provenance proves coordinates, not a line's semantic role.
        # A later prose mention alone must not become a bibliography boundary.
        if patterns.is_reference_heading(b.text.splitlines()[0], allow_numbered=False):
            span = _until_reference_end_heading(
                _after_heading(
                    blocks,
                    i,
                    len(blocks),
                    patterns,
                    issues,
                    allow_numbered=False,
                    headings=headings,
                ),
                patterns,
                b,
                issues,
                protected=protected,
                anchor_kind=anchor_kind,
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
            or _standalone_label(item.text)
            or _parse_group([item.text]).parse_status == CitationParseStatus.parsed
            for item in tail
        ):
            continue
        previous_groups = _segment_blocks(preceding, anchor_kind)
        if not previous_groups:
            continue
        last = _parse_group([item.text for item in previous_groups[-1]])
        if not (last.parse_status == CitationParseStatus.parsed or last.url or last.doi):
            continue
        return preceding
    return span


def _until_reference_end_heading(
    span: list[TextBlock],
    patterns: HeadingPatterns,
    reference_heading: TextBlock,
    issues: list[str],
    *,
    protected: list[TextBlock] | None = None,
    anchor_kind: str = "page",
    outline_end: SectionNode | None = None,
) -> list[TextBlock]:
    for index, block in enumerate(span):
        # Match an entire original source line, not merely "Received" inside
        # a title. Native block packing must not change bibliography membership.
        offset = 0
        slicer = SourceSlicer(block)
        for line in block.text.splitlines(keepends=True):
            start, offset = offset, offset + len(line)
            history = any(p.fullmatch(normalize(line)) for p in patterns.reference_history_lines)
            heading_block = slicer.slice(start, offset)
            outline_match = outline_end is not None and _isolated_heading(
                heading_block, outline_end, patterns
            )
            end_heading = outline_match or any(
                p.fullmatch(normalize(line)) for p in patterns.reference_end_headings
            )
            if not (history or end_heading):
                continue
            prefix = [slicer.slice(0, start)] if block.text[:start].strip() else []
            preceding = [*span[:index], *prefix]
            tail = [slicer.slice(offset, len(block.text))] if block.text[offset:].strip() else []
            if history and not preceding:
                continue
            if end_heading:
                if start and heading_block.source_lines is None:
                    issues.append("ambiguous_reference_end_style")
                    return span
                if not outline_match and not _contrasting_end_heading(
                    heading_block, preceding, reference_heading, patterns
                ):
                    if _bibliography_continues([*tail, *span[index + 1 :]]):
                        issues.append("ambiguous_reference_end_style")
                        return span
                    continue
            if _bibliography_continues([*tail, *span[index + 1 :]]):
                # A supported closing heading can bound a complete primary
                # bibliography without classifying the later appendix. Keep
                # that entire later region protected from body/linkage use.
                groups = (
                    _segment_blocks(preceding, anchor_kind)
                    if _PREVIOUS_READER.get()
                    else _entry_groups(preceding, anchor_kind)
                )
                if (
                    end_heading
                    and protected is not None
                    and groups
                    and all(
                        _complete_entry([part.text for part in group], order)
                        for order, group in enumerate(groups)
                    )
                ):
                    protected.extend([slicer.slice(start, len(block.text)), *span[index + 1 :]])
                    return preceding
                # A nested bibliography or an appendix containing references:
                # preserve source and do not turn its entries into body claims.
                issues.append("ambiguous_reference_end")
                return span
            return preceding
    return span


def _entry_groups(span: list[TextBlock], anchor_kind: str) -> list[list[TextBlock]] | None:
    """Entries as the selection reads them, with a labels column rebuilt.

    An entry that is only its label proves the labels were read apart from
    their entries; then the entries must be rebuilt from line positions, or
    None is returned. A label-only block inside a larger group is weaker
    evidence (right-aligned "[9]" and "[10]" start at different x positions),
    so it is used only when the rebuild succeeds.
    """
    groups = _segment_blocks(span, anchor_kind)
    if any(_standalone_label(_merge([block.text for block in group])) for group in groups):
        return _pair_detached_labels(span)
    if not _PREVIOUS_READER.get() and any(_standalone_label(block.text) for block in span):
        return _pair_detached_labels(span) or groups
    return groups


def _complete_entry(texts: list[str], order_index: int) -> bool:
    """Whether an entry before a closing heading was printed in full.

    It parses, or it prints a reference date followed by more text that
    reaches its locator (a DOI or address) or closes its last sentence. An
    entry cut short by the heading ("Santos, M. (", "Cruz, A. (2022") is none
    of these, so the list's end stays unclear rather than trusted.
    """
    if _parse_group(texts, order_index).parse_status == CitationParseStatus.parsed:
        return True
    if _PREVIOUS_READER.get():
        return False
    view = _shown_entry("\n".join(texts))
    try:
        date = date_patterns()["reference"].search(view)
    except (OSError, ValueError, UnicodeError):
        return False
    if date is None or not any(char.isalpha() for char in view[date.end() :]):
        return False
    # The locator is printed last, so an entry that reached it after its
    # date was not cut short, even if its address wraps onto another line.
    # Without one, the entry must at least close its last sentence.
    return view.rstrip().endswith((".", "?", "!")) or any(
        locator.start() >= date.end()
        for locator in [*_URL.finditer(view), *_DOI_TAIL.finditer(view)]
    )


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


def _standalone_label(text: str) -> bool:
    """A list label printed on a line of its own: "[12]", or "12." up to the
    list-number limit. "7534015." alone is the tail of a wrapped identifier."""
    if _STANDALONE_REFERENCE_LABEL.fullmatch(text) is None:
        return False
    if text.lstrip().startswith("["):
        return True
    number = _parse_reference_number(text.strip()[:-1])
    return number is not None and number <= get_settings().reference_list_label_max


def _first_line_box(block: TextBlock) -> Bbox | None:
    lines = block.source_lines or []
    return lines[0].bbox if lines else block.bbox


def _label_number(text: str) -> tuple[str, int | None]:
    """The style ("[]", "." or ")") and number of a standalone label."""
    stripped = text.strip()
    if stripped.startswith("["):
        return "[]", _parse_reference_number(stripped[1:-1])
    return stripped[-1], _parse_reference_number(stripped[:-1])


def _one_column(boxes: list[Bbox]) -> bool:
    """Every box spans a common x position, so they stand in one column."""
    return max(box[0] for box in boxes) < min(box[2] for box in boxes)


def _pair_detached_labels(span: list[TextBlock]) -> list[list[TextBlock]] | None:
    """Rebuild entries from a labels column printed beside a text column.

    Each label belongs to the one text block to its right whose first printed
    line holds the label's vertical centre on the same page. Text blocks that
    start no entry continue the entry before them. The labels must stand in
    one column, the text in one column to their right, and the labels must be
    of one style and numbered without a gap. Anything else (a label with no text
    on its line, two labels on one line, text before the first label, a
    second column) means the entries cannot be trusted, and None is returned.
    """
    labels: list[TextBlock] = []
    texts: list[TextBlock] = []
    for block in span:
        if block.page is None or _first_line_box(block) is None:
            return None
        if _standalone_label(block.text):
            labels.append(block)
        elif block.text.strip():
            texts.append(block)
    if not labels or not texts:
        return None
    label_boxes = [_first_line_box(label) for label in labels]
    text_boxes = [_first_line_box(text) for text in texts]
    labels_right_edge = min(box[2] for box in label_boxes)
    if (
        not _one_column(label_boxes)
        or not _one_column(text_boxes)
        or any(box[0] < labels_right_edge for box in text_boxes)
    ):
        return None
    starts: dict[int, TextBlock] = {}
    for label in labels:
        box = _first_line_box(label)
        centre = (box[1] + box[3]) / 2
        holders = [
            text
            for text in texts
            if text.page == label.page
            and _first_line_box(text)[1] <= centre <= _first_line_box(text)[3]
        ]
        if len(holders) != 1 or id(holders[0]) in starts:
            return None
        starts[id(holders[0])] = label
    groups: list[list[TextBlock]] = []
    for text in sorted(texts, key=lambda block: (block.page, _first_line_box(block)[1])):
        label = starts.get(id(text))
        if label is not None:
            groups.append([label, text])
        elif groups:
            groups[-1].append(text)
        else:
            return None
    if len(groups) != len(labels):
        return None
    numbered = [_label_number(group[0].text) for group in groups]
    styles = {style for style, _number in numbered}
    numbers = [number for _style, number in numbered]
    if len(styles) != 1 or None in numbers:
        return None
    # A lost label would merge its entry into the one before, so the numbers
    # must run on without a gap.
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        return None
    return groups


def _bibliography_continues(span: list[TextBlock]) -> bool:
    return any(
        _NUMBERED_ENTRY.match(block.text) is not None
        or _standalone_label(block.text)
        or date_patterns()["entry"].match(block.text) is not None
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
    # A list that indents each entry's first line instead (later lines flush
    # left) starts its entries at that indent; its flush-left lines continue.
    xs = [b.bbox[0] for b in span if b.bbox is not None]
    entry_x = min(xs) if xs else 0.0
    indent_x = _first_line_indent(span, entry_x)
    entries: list[list[TextBlock]] = []
    current: list[TextBlock] = []
    for b in span:
        x0 = b.bbox[0] if b.bbox is not None else entry_x
        starts = (
            abs(x0 - indent_x) <= _INDENT_JITTER_PTS
            if indent_x is not None
            else x0 <= entry_x + _INDENT_JITTER_PTS
        )
        if starts and current:
            entries.append(current)
            current = []
        current.append(b)
    if current:
        entries.append(current)
    return [group for group in entries if _merge([block.text for block in group])]


def _first_line_indent(span: list[TextBlock], leftmost: float) -> float | None:
    """The indent where entries start, for a list that indents first lines.

    The structural evidence is the list's own first entry: it must begin
    indented, right of the flush-left lines, with an author-and-date opening.
    Most openings must then sit at that same indent. A hanging-indent list
    always begins flush left, so a wrapped author line that happens to read as
    an opening ("G. D. (2024). Title") cannot switch it to this reading.
    Mixed evidence returns None and the hanging-indent reading holds.
    """
    position, conflict = _first_line_indent_evidence(span, leftmost)
    return None if conflict else position


def _first_line_indent_evidence(
    span: list[TextBlock], leftmost: float
) -> tuple[float | None, bool]:
    """The first-line indent the list shows, and whether it also shows an
    entry that contradicts it.

    The contradiction is a full author-and-date opening ("Cruz, A. (2020).")
    away from the indent, right after a line that could end an entry. A
    wrapped line of initials ("D. (2021). Title") or a line continuing an
    unfinished one sits flush left in such a list and is not one. Such an
    entry would be merged into the one above it, so the entries are refused.
    """
    first = next((b for b in span if b.text.strip()), None)
    if _PREVIOUS_READER.get() or first is None or first.bbox is None:
        return None, False
    position = first.bbox[0]
    try:
        entry = date_patterns()["entry"]
    except (OSError, ValueError, UnicodeError):
        return None, False  # no date grammar, no layout evidence
    if position - leftmost <= _INDENT_JITTER_PTS or not entry.match(first.text):
        return None, False
    openings = [b for b in span if b.bbox is not None and entry.match(b.text)]
    at_indent = sum(abs(b.bbox[0] - position) <= _INDENT_JITTER_PTS for b in openings)
    settings = get_settings()
    if (
        at_indent < settings.reference_first_line_indent_min_openings
        or at_indent / len(openings) < settings.reference_first_line_indent_min_share
    ):
        return None, False
    conflict = any(
        block.bbox is not None
        and entry.match(block.text)
        and abs(block.bbox[0] - position) > _INDENT_JITTER_PTS
        and not _initials_only_opening(block.text)
        and _closes_entry(previous.text)
        for previous, block in zip(span, span[1:], strict=False)
    )
    return position, conflict


def _closes_entry(text: str) -> bool:
    """Whether a line could be an entry's last: it ends on a locator, or on a
    period that is not an author's initial. A line that stops mid-phrase
    ("... system for the") runs on, so the next line continues it."""
    words = text.split()
    if not words:
        return False
    last = words[-1]
    if _URL.fullmatch(last) or _DOI_TAIL.fullmatch(last):
        return True
    return last.endswith(".") and not _INITIALS.fullmatch(last)


def _initials_only_opening(text: str) -> bool:
    """ "G. D. (2024). Title": the tail of an author list wrapped onto a new line."""
    words = [
        word
        for word in re.split(r"[\s,&]+", text[: text.find("(")])
        if word and word.casefold() != "and"
    ]
    return bool(words) and all(_INITIALS.fullmatch(word) for word in words)


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
    number = _parse_reference_number(match.group(1) or match.group(2))
    if match.group(1) and number is not None and number > get_settings().reference_list_label_max:
        # "2023. URL https://..." continues an entry; it is not entry 2023.
        return None
    return number


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
