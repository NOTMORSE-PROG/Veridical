"""In-text citation extraction + cross-match against the reference list
(F5.1). Regex-first, same convention as `app.ingest.references` (V-006):
APA in-text citation shapes (parenthetical, narrative, multi-cite groups,
"et al.", year-letter disambiguation) are format invariants of the citation
style, not tunable thresholds, so they're compiled patterns here — not a
rubric-supplied data file, and not an LLM call (quota is the currency,
PLAYBOOK §2; this is a bounded pattern-matching problem the same way
reference-list parsing already was, measured 17/17 on the real demo
document).

Secondary citations ("as cited in") are detected and excluded from cross-
matching entirely (ticket edge case: "flag as secondary, don't resolve the
inner source"). A secondary citation always names an author who will not
be in THIS manuscript's own reference list by construction — resolving it
would either guarantee a false orphan or require guessing which of two
named sources is the one actually listed. Precision over recall (charter
judgment #1): when a citation shape is ambiguous, don't flag.
"""

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.checks.citations.numbered import reference_anchor
from app.config import Settings, get_settings
from app.findings.identity import FINDING_GROUP_FIELD, finding_group_fingerprint
from app.ingest.citation_dates import date_patterns
from app.ingest.schemas import TextBlock
from app.models.enums import FlagSeverity

# --- reference-list side: the minimal shape this module needs from a
# structured citation. `models.citation.Citation` satisfies this
# structurally (same convention as `app.checks.rules.CitationLike`) without
# this module importing the ORM or the F3 rule engine's own Protocol. ------


class ReferenceLike(Protocol):
    order_index: int
    raw_text: str
    authors: list[str] | None
    year: int | None


# --- patterns --------------------------------------------------------------
#
# Deliberately NOT compiled with re.IGNORECASE: the leading capital-letter
# requirement on every name word is what stops a match from bleeding
# backward into ordinary lowercase prose that happens to precede a
# "(YYYY)"-shaped span ("...but did not actually say what the citing paper
# claimed it said. Haan (2025)" must extract "Haan", not the whole
# sentence) — case-insensitivity would let `[A-ZÀ-Þ]` match any lowercase
# letter too, defeating that boundary (found live against the real demo
# document, ticket AC #1's hand-verification pass). Only the "as cited in"/
# page-locator keywords need case-insensitivity, so those get a *scoped*
# inline `(?i:...)` group instead of a document-wide flag.

_SECONDARY_MARK = re.compile(r"as\s+cited\s+in", re.IGNORECASE)
# The work a secondary citation was read in IS cited: "(Moreno, 1998, as
# cited in Iommi, 2025)" cites Iommi 2025, which belongs in the reference list.
_SECONDARY_OUTER = re.compile(r"as\s+cited\s+in\s+(?P<outer>[^()]+?)\s*$", re.IGNORECASE)
# A look-back for "As cited in Jones (2020)" must stay inside its sentence: a
# previous sentence ending "(..., as cited in X, 2020)." says nothing about
# the next citation (2026-10-07: that leak marked cited sources as unused).
_LOOKBACK_STOP = re.compile(r"[.!?;)]")

# A whole parenthetical citation group: everything inside one set of parens
# is captured without nesting. A year is checked separately, then each
# bounded entry is token-parsed without the old nested author expression.
_PAREN_GROUP = re.compile(r"\(([^()]*)\)")
_YEAR_IN_GROUP = re.compile(r"\d{4}[a-z]?|n\.?\s*d\.", re.IGNORECASE)

# One citation within a parenthetical group (post ';' split): "Author(s),
# Year" with an optional page/paragraph locator and/or secondary-source tail.
_PAREN_YEAR = re.compile(r",\s*(?P<year>\d{4}[a-z]?|n\.?\s*d\.)(?=,|\s*$)", re.IGNORECASE)
_PAREN_EXTRA_YEARS = re.compile(r"(?:,\s*(?:\d{4}[a-z]?|n\.?\s*d\.)(?=,|\s*$))+", re.IGNORECASE)
_PAREN_ENTRY = _PAREN_YEAR
_PAREN_TAIL = re.compile(
    r"(?:,\s*(?i:pp?\.?|para\.?|section)\s*[\w\-–]+)?"
    r"(?:,\s*(?i:as\s+cited\s+in)\s+[^()]*)?\s*$"
)

# Find the year first. Searching the nested author grammar over an entire PDF
# block can backtrack exponentially on capitalized glossary entries that have
# no citation year at all. The author is parsed as a same-line token suffix
# only after a year-shaped citation has been found.
_NARRATIVE_YEAR = re.compile(r"\((?P<year>\d{4}[a-z]?|n\.?\s*d\.)", re.IGNORECASE)
_NARRATIVE = _NARRATIVE_YEAR
_ET_AL_FINAL = re.compile(r"al\.?,?")
_INITIALS_TOKEN = re.compile(r"(?:[^\W\d_]\.)+(?:-[^\W\d_]\.)*")
# Hyphens include U+2010/U+2011, which publisher metadata and PDFs use in
# names ("Crnojevic‐Bengin"); U+2060 marks a surname hyphenated across a line.
_NARRATIVE_NAME_TOKEN = re.compile(r"[A-ZÀ-Þ][\w'’.\-‐‑⁠]*,?")
# "Garcia-" at a line end + "Cobo" on the next line is one hyphenated name.
_HYPHENATED_NAME_BREAK = re.compile(r"(?<=\w)[-‐‑]\n(?=[A-ZÀ-Þ])")
# Lowercase name particles a surname may contain ("Soares dos Santos",
# "de los Reyes", "bin Ahmad"), plus the joining words of an author list.
_NARRATIVE_CONNECTORS = frozenset(
    {
        *("of", "on", "for", "and", "the", "&", ","),
        *("de", "del", "dela", "delos", "los", "las", "van", "von", "der", "den", "ter", "ten"),
        *("da", "das", "do", "dos", "di", "du", "la", "le", "y", "bin", "binti", "al", "el"),
    }
)

# Lowercase words that may sit inside a surname (no capitalized prose).
NAME_PARTICLES = _NARRATIVE_CONNECTORS - {"of", "on", "for", "and", "the", "&", ","}

# Same convention as V-006's `_PART_SPLIT`: a period/!/? followed by
# whitespace and a capital/digit/opening-quote starts a new sentence.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")

# BUG-240: the rule above splits "Chen et al. (2024) showed X" after "al.",
# because "(" is in its lookahead class. The real 47-page run therefore sent
# claims like `Chen et al.` and `Li et al.` to claim-support grading. Those
# are author fragments, not propositions an abstract can support or
# contradict, so a schema-valid model answer to them is meaningless and the
# quota is spent for nothing (charter judgment 2).
#
# These are orthographic invariants of academic prose, not a policy an
# instructor could reasonably disagree with, so they live here beside the
# boundary rule they correct rather than in a config file (ground rule 7
# governs rubric/threshold policy, not spelling).
_ABBREVIATIONS = frozenset(
    {
        # scholarly
        "al",
        "cf",
        "e.g",
        "etc",
        "i.e",
        "viz",
        "vs",
        "ca",
        "approx",
        # bibliographic
        "ed",
        "eds",
        "edn",
        "eq",
        "eqs",
        "fig",
        "figs",
        "no",
        "p",
        "pp",
        "ref",
        "refs",
        "repr",
        "rev",
        "sec",
        "tab",
        "trans",
        "vol",
        "vols",
        # titles and organisations that precede a capitalised name
        "dept",
        "dr",
        "inc",
        "jr",
        "ltd",
        "mr",
        "mrs",
        "ms",
        "prof",
        "sr",
        "st",
        "univ",
    }
)

# The token immediately before a candidate boundary, with its closing period.
_TRAILING_TOKEN = re.compile(r"([A-Za-z][A-Za-z.]*)\.$")

# A line ending mid-word at a hyphen, e.g. "A neuro-" wrapping to "symbolic".
# Requires a letter on both sides of the hyphen so a dash used as punctuation
# ("the result -") does not swallow the following line's leading space.
_HYPHEN_BREAK = re.compile(r"[^\W\d_]-$", re.UNICODE)

_LOOKBACK_CHARS = 40  # window checked before a narrative match for a
# preceding "as cited in" (the "As cited in Jones (2020), ..." phrasing).

# A style/coverage detector, not numeric-citation linkage. Source-number
# labels may lose a space during PDF extraction, or reference segmentation
# may miss the bibliography entirely. A bracket-shaped body mark alone can
# therefore trigger incomplete coverage, but never a link or accusation.
_NUMBERED_REFERENCE = re.compile(r"^\s*(?:\[\d+\]|\(\d+\)|\d+[.)])(?:\s|(?=[A-Z])|$)")
_BRACKETED_BODY_CANDIDATE = re.compile(r"(?<![\w])\[\s*[1-9]\d*(?:\s*[,;\-–]\s*[1-9]\d*)*\s*\]")


@dataclass(frozen=True)
class InTextCitation:
    """One in-text citation instance, anchored to where it was found."""

    surnames: list[str]
    year: str  # raw: digits + optional letter suffix, e.g. "2024" or "2024a"
    is_narrative: bool
    is_secondary: bool
    raw_text: str
    anchor: str
    page: int | None
    paragraph: int | None
    claim_sentence: str
    claim_excerpt_cropped: bool = False


@dataclass(frozen=True)
class CrossMatchResult:
    in_text: list[InTextCitation]
    linked: list[tuple[InTextCitation, list[ReferenceLike]]]
    orphans: list[InTextCitation]  # in-text citations with no matching reference
    uncited: list[ReferenceLike]  # raw unmatched candidates, not proven unused
    ambiguous: list[InTextCitation]  # several sources fit one in-text mark
    possibly_cited: list[ReferenceLike] = field(default_factory=list)
    confidently_uncited: list[ReferenceLike] = field(default_factory=list)


@dataclass(frozen=True)
class CitationFlagDraft:
    """Flag-ready shape (severity + honest wording already applied) for
    V-029's citation-integrity stage to persist as a real `Flag` row
    alongside its own existence/retraction findings — kept here, not
    re-derived, so this wording lives in exactly one place (charter rule 3)."""

    severity: FlagSeverity
    evidence_excerpt: str
    page_anchor: str
    detail: dict[str, Any]


@dataclass(frozen=True)
class NumberedStyleCoverage:
    n_numbered_reference_entries: int
    n_numeric_body_candidates: int

    def linkage_unassessed(self, *, n_references: int, n_author_year_uncited: int) -> bool:
        # Bracket marks can be mathematical notation in an otherwise fully
        # linked author-year paper. A body mark alone is a gap only when no
        # references were recovered or the author-year matcher has leftovers.
        return self.n_numbered_reference_entries > 0 or (
            self.n_numeric_body_candidates > 0 and (n_references == 0 or n_author_year_uncited > 0)
        )


def numbered_style_coverage(
    blocks: list[TextBlock], references: list[ReferenceLike]
) -> NumberedStyleCoverage:
    """Count source shapes; do not infer linkage or genuine unused entries."""
    return NumberedStyleCoverage(
        n_numbered_reference_entries=sum(
            bool(_NUMBERED_REFERENCE.match(ref.raw_text or "")) for ref in references
        ),
        n_numeric_body_candidates=sum(
            len(_BRACKETED_BODY_CANDIDATE.findall(block.text or "")) for block in blocks
        ),
    )


ORPHAN_WORDING = (
    "In-text citation to '{authors}, {year}' has no matching entry in the "
    "reference list. Possible missing reference, a typo in the author name "
    "or year, or a citation style this checker didn't recognize — please "
    "check manually."
)
UNCITED_WORDING = (
    "Reference list entry '{first_author}' ({year}) does not appear to be "
    "cited anywhere in the body text. Possible unused reference, or an "
    "in-text citation style this checker didn't recognize — please check "
    "manually."
)


# --- extraction --------------------------------------------------------------


def extract_in_text_citations(
    blocks: list[TextBlock],
    *,
    settings: Settings | None = None,
    coverage_gaps: set[str] | None = None,
) -> list[InTextCitation]:
    """Scan `blocks` (already filtered to exclude the reference-list span
    and furniture — see `app.ingest.references.non_reference_blocks`) for
    APA in-text citation shapes."""
    settings = settings or get_settings()
    out: list[InTextCitation] = []
    gaps = coverage_gaps if coverage_gaps is not None else set()
    # BUG-240 AC1: a PDF block is a physical line. Scanning them one at a
    # time truncated claims at the line break ("Haan (2025), with"), and a
    # half-line is not a proposition an abstract can support or contradict.
    for block in merge_wrapped_lines(blocks):
        text = block.text
        if not text:
            continue
        spans = _sentence_spans(text)
        anchor = _anchor_for(block)

        for group_m in _PAREN_GROUP.finditer(text):
            if len(out) >= settings.citation_in_text_max_matches:
                gaps.add("citation_count_limit")
                return out
            if not date_patterns(settings)["group"].search(group_m.group(1)):
                continue
            if len(group_m.group(1)) > settings.citation_parenthetical_group_max_chars:
                gaps.add("parenthetical_group_limit")
                continue
            for raw_entry in group_m.group(1).split(";"):
                raw_entry = raw_entry.strip()
                if not raw_entry:
                    continue
                parsed = _parenthetical_entry(raw_entry, settings, gaps)
                if parsed is None:
                    continue
                if len(out) >= settings.citation_in_text_max_matches:
                    gaps.add("citation_count_limit")
                    return out
                surnames, year, extra_years = parsed
                claim, cropped = _sentence_at(
                    text,
                    group_m.start(),
                    group_m.end(),
                    spans,
                    max_chars=settings.citation_claim_excerpt_max_chars,
                )
                is_secondary = bool(_SECONDARY_MARK.search(raw_entry))
                found = [(surnames, y, is_secondary) for y in (year, *extra_years)]
                outer = _SECONDARY_OUTER.search(raw_entry) if is_secondary else None
                if outer is not None:
                    outer_parsed = _parenthetical_entry(outer.group("outer"), settings, gaps)
                    if outer_parsed is not None:
                        found.extend(
                            (outer_parsed[0], y, False) for y in (outer_parsed[1], *outer_parsed[2])
                        )
                for found_surnames, found_year, found_secondary in found:
                    out.append(
                        InTextCitation(
                            surnames=found_surnames,
                            year=found_year,
                            is_narrative=False,
                            is_secondary=found_secondary,
                            raw_text=group_m.group(0),
                            anchor=anchor,
                            page=block.page,
                            paragraph=block.paragraph,
                            claim_sentence=claim,
                            claim_excerpt_cropped=cropped,
                        )
                    )

        for m in date_patterns(settings)["narrative"].finditer(text):
            if len(out) >= settings.citation_in_text_max_matches:
                gaps.add("citation_count_limit")
                return out
            year_end = _narrative_year_end(text, m.end(), settings, gaps)
            if year_end is None:
                continue
            author_span = _narrative_author_span(
                text,
                m.start(),
                settings.citation_author_max_chars,
                settings.citation_author_max_tokens,
                settings.citation_unseparated_author_max_tokens,
                gaps,
            )
            if author_span is None:
                continue
            start, authors = author_span
            surnames = _split_authors(authors)
            if not surnames:
                continue
            preceding = text[max(0, start - _LOOKBACK_CHARS) : start]
            stops = list(_LOOKBACK_STOP.finditer(preceding))
            if stops:
                preceding = preceding[stops[-1].end() :]
            raw_text = text[start:year_end]
            is_secondary = bool(_SECONDARY_MARK.search(raw_text)) or bool(
                _SECONDARY_MARK.search(preceding)
            )
            outer = _SECONDARY_OUTER.search(raw_text[:-1]) if raw_text.endswith(")") else None
            if outer is not None:
                outer_parsed = _parenthetical_entry(outer.group("outer"), settings, gaps)
                if outer_parsed is not None:
                    claim, cropped = _sentence_at(
                        text,
                        start,
                        year_end,
                        spans,
                        max_chars=settings.citation_claim_excerpt_max_chars,
                    )
                    for outer_year in (outer_parsed[1], *outer_parsed[2]):
                        out.append(
                            InTextCitation(
                                surnames=outer_parsed[0],
                                year=outer_year,
                                is_narrative=False,
                                is_secondary=False,
                                raw_text=raw_text,
                                anchor=anchor,
                                page=block.page,
                                paragraph=block.paragraph,
                                claim_sentence=claim,
                                claim_excerpt_cropped=cropped,
                            )
                        )
            claim, cropped = _sentence_at(
                text,
                start,
                year_end,
                spans,
                max_chars=settings.citation_claim_excerpt_max_chars,
            )
            raw_narrative_y = m.group("year")
            norm_narrative_y = (
                "n.d."
                if "nd" in raw_narrative_y.lower().replace(".", "").replace(" ", "")
                else raw_narrative_y
            )
            out.append(
                InTextCitation(
                    surnames=surnames,
                    year=norm_narrative_y,
                    is_narrative=True,
                    is_secondary=is_secondary,
                    raw_text=raw_text,
                    anchor=anchor,
                    page=block.page,
                    paragraph=block.paragraph,
                    claim_sentence=claim,
                    claim_excerpt_cropped=cropped,
                )
            )
    return out


def _parenthetical_entry(
    entry: str, settings: Settings, gaps: set[str]
) -> tuple[list[str], str, tuple[str, ...]] | None:
    """Authors, first year, and any further years of the same authors.

    APA cites several works by the same authors with one name list:
    "(Biggs & Tang, 2007, 2011)". Before 2026-10-07 the second year made the
    tail unreadable, which withheld every citation finding in the paper.
    """
    if len(entry) > settings.citation_parenthetical_entry_max_chars:
        gaps.add("parenthetical_entry_limit")
        return None
    for year_match in date_patterns(settings)["parenthetical"].finditer(entry):
        author = entry[: year_match.start()].strip()
        if not _author_tokens_valid(
            author,
            settings.citation_author_max_chars,
            settings.citation_author_max_tokens,
            settings.citation_unseparated_author_max_tokens,
        ):
            if _author_limit_exceeded(
                author,
                settings.citation_author_max_chars,
                settings.citation_author_max_tokens,
                settings.citation_unseparated_author_max_tokens,
            ):
                gaps.add("author_limit")
            continue
        tail = entry[year_match.end() :]
        extra = _PAREN_EXTRA_YEARS.match(tail)
        extra_years = (
            tuple(
                "n.d." if "nd" in y.lower().replace(".", "").replace(" ", "") else y
                for y in re.findall(r"\d{4}[a-z]?|n\.?\s*d\.", extra.group(0), re.IGNORECASE)
            )
            if extra
            else ()
        )
        if extra:
            tail = tail[extra.end() :]
        if not _PAREN_TAIL.fullmatch(tail):
            gaps.add("parenthetical_tail_unreadable")
            return None
        raw_y = year_match.group("year")
        norm_y = "n.d." if "nd" in raw_y.lower().replace(".", "").replace(" ", "") else raw_y
        return _split_authors(author), norm_y, extra_years
    return None


def _author_limit_exceeded(
    author: str, max_chars: int, max_tokens: int, max_unseparated_tokens: int
) -> bool:
    tokens = author.split()
    has_author_separator = bool(re.search(r",|&|\bet\s+al\b", author)) or any(
        token in _NARRATIVE_CONNECTORS for token in tokens
    )
    return (
        len(author) > max_chars
        or len(tokens) > max_tokens
        or (len(tokens) > max_unseparated_tokens and not has_author_separator)
    )


def _author_tokens_valid(
    author: str, max_chars: int, max_tokens: int, max_unseparated_tokens: int
) -> bool:
    if not author or _author_limit_exceeded(author, max_chars, max_tokens, max_unseparated_tokens):
        return False
    tokens = author.split()
    if len(tokens) > max_tokens:
        return False
    if len(tokens) >= 2 and tokens[-2] == "et" and _ET_AL_FINAL.fullmatch(tokens[-1]):
        tokens = tokens[:-2]
    if not tokens or not _NARRATIVE_NAME_TOKEN.fullmatch(tokens[0]):
        return False
    if not _NARRATIVE_NAME_TOKEN.fullmatch(tokens[-1]):
        return False
    return all(
        _NARRATIVE_NAME_TOKEN.fullmatch(token) or token in _NARRATIVE_CONNECTORS for token in tokens
    )


def _narrative_year_end(text: str, year_end: int, settings: Settings, gaps: set[str]) -> int | None:
    if text[year_end : year_end + 1] == ")":
        return year_end + 1
    if text[year_end : year_end + 1] != ",":
        return None
    limit = min(len(text), year_end + settings.citation_parenthetical_entry_max_chars)
    closing = text.find(")", year_end + 1, limit)
    if closing < 0:
        gaps.add("narrative_tail_unreadable")
        return None
    tail = text[year_end + 1 : closing]
    if not re.fullmatch(r"\s*(?i:as\s+cited\s+in)\s+[^()]+", tail):
        return None
    return closing + 1


def _narrative_author_span(
    text: str,
    year_start: int,
    max_chars: int,
    max_tokens: int,
    max_unseparated_tokens: int,
    gaps: set[str],
) -> tuple[int, str] | None:
    """Parse at most one wrapped author suffix in a bounded source window."""
    if year_start == 0 or text[year_start - 1] not in " \t\n":
        return None
    window_start = max(0, year_start - max_chars)
    # A surname hyphenated across a line is one token. The substitution keeps
    # the length, so token offsets still index `text`.
    prefix = _HYPHENATED_NAME_BREAK.sub(
        lambda match: match.group(0)[0] + "⁠", text[window_start:year_start]
    )
    tokens = list(re.finditer(r"\S+", prefix))
    if not tokens:
        return None
    last_break = prefix.rfind("\n")
    current_line_has_name = any(
        token.start() > last_break and _NARRATIVE_NAME_TOKEN.fullmatch(token.group())
        for token in tokens
    )
    previous_line = prefix[:last_break].rstrip() if last_break >= 0 else ""
    previous_tail = previous_line.split()[-1] if previous_line.split() else ""
    crosses_line = (
        not current_line_has_name
        or previous_tail in _NARRATIVE_CONNECTORS
        or previous_tail.endswith(",")
    )
    first_current_token = next(
        (index for index, token in enumerate(tokens) if token.start() > last_break), len(tokens)
    )
    current_line_starts_with_connector = (
        first_current_token < len(tokens)
        and tokens[first_current_token].group() in _NARRATIVE_CONNECTORS
    )
    crosses_line = crosses_line or current_line_starts_with_connector
    previous_line_fragments = [line.strip() for line in previous_line.split("\n")]
    previous_line_fragment = previous_line_fragments[-1]
    preceding_single_name_lines = len(previous_line_fragments) >= 2 and all(
        len(line.split()) == 1 and _NARRATIVE_NAME_TOKEN.fullmatch(line)
        for line in previous_line_fragments[-2:]
    )
    # Only the previous line's last sentence fragment can continue a name:
    # "...work on records (Jun, 2010). Several" ends a sentence before
    # "Several", so its "on" says nothing about an author list.
    tail_fragment = re.split(r"[.!?;)]\s+", previous_line_fragment)[-1]
    preceding_connector_line = any(
        token in _NARRATIVE_CONNECTORS for token in tail_fragment.split()
    )
    earlier_line_ends_connector = (
        len(previous_line_fragments) >= 2
        and previous_line_fragments[-2].split()
        and previous_line_fragments[-2].split()[-1] in _NARRATIVE_CONNECTORS
    )
    # The previous line can only hold part of this author list when the
    # name run starts the current line. "...engagement. Bautista (2018)" has
    # a complete sentence before the name on its own line.
    run_end = len(tokens) - 1
    if (
        run_end > 0
        and _ET_AL_FINAL.fullmatch(tokens[run_end].group())
        and tokens[run_end - 1].group() == "et"
    ):
        run_end -= 2
    run_start = run_end
    while run_start > first_current_token and (
        _NARRATIVE_NAME_TOKEN.fullmatch(tokens[run_start - 1].group())
        or tokens[run_start - 1].group() in _NARRATIVE_CONNECTORS
    ):
        run_start -= 1
    name_run_starts_line = run_start <= first_current_token
    if (
        not crosses_line
        and name_run_starts_line
        and last_break >= 0
        and _NARRATIVE_NAME_TOKEN.fullmatch(previous_tail)
        and (preceding_single_name_lines or preceding_connector_line or earlier_line_ends_connector)
        and not previous_line.endswith((".", "!", "?", ";", ":"))
    ):
        gaps.add("author_line_wrap_ambiguous")
        return None
    lower_bound = 0 if crosses_line else first_current_token
    index = len(tokens) - 1
    if (
        _ET_AL_FINAL.fullmatch(tokens[index].group())
        and index > 0
        and tokens[index - 1].group() == "et"
    ):
        index -= 2
    if index < 0 or not _NARRATIVE_NAME_TOKEN.fullmatch(tokens[index].group()):
        return None

    first_name = index
    line_breaks = 0
    while first_name > lower_bound:
        prior = first_name - 1
        gap_text = prefix[tokens[prior].end() : tokens[first_name].start()]
        line_breaks += gap_text.count("\n")
        if line_breaks > 1:
            gaps.add("author_line_wrap_limit")
            return None
        token = tokens[prior].group()
        if _NARRATIVE_NAME_TOKEN.fullmatch(token) or token in _NARRATIVE_CONNECTORS:
            first_name = prior
        else:
            break
    while first_name < len(tokens) and not _NARRATIVE_NAME_TOKEN.fullmatch(
        tokens[first_name].group()
    ):
        first_name += 1
    if first_name >= len(tokens):
        return None
    start = window_start + tokens[first_name].start()
    if first_name == 0 and window_start > 0 and text[window_start - 1] not in ".!?;:\n":
        gaps.add("author_limit")
        return None
    author = text[start:year_start].strip()
    if not _author_tokens_valid(author, max_chars, max_tokens, max_unseparated_tokens):
        gaps.add(
            "author_limit"
            if _author_limit_exceeded(author, max_chars, max_tokens, max_unseparated_tokens)
            else "author_unreadable"
        )
        return None
    return start, author


CORPORATE_AUTHOR_WORDS = frozenset(
    {
        "university",
        "institute",
        "institution",
        "department",
        "council",
        "commission",
        "agency",
        "organization",
        "organisation",
        "association",
        "committee",
        "office",
        "laboratory",
        "team",
        "ministry",
        "authority",
        "center",
        "centre",
        "bureau",
        "board",
        "foundation",
        "society",
        "federation",
        "corporation",
        "administration",
    }
)


def _is_corporate_author_name(name: str) -> bool:
    tokens = {word.strip(".,;:()").lower() for word in name.split()}
    return bool(tokens & CORPORATE_AUTHOR_WORDS)


def _split_corporate_authors(raw: str) -> list[str]:
    """Split multiple corporate author names joined by 'and' or '&' without commas,
    e.g. 'Food and Agriculture Organization and World Health Organization' ->
    ['Food and Agriculture Organization', 'World Health Organization'].
    """
    pattern = re.compile(
        r"\b(" + "|".join(CORPORATE_AUTHOR_WORDS) + r")\s+(?:and|&)\s+",
        re.IGNORECASE,
    )
    splits: list[str] = []
    last_end = 0
    for m in pattern.finditer(raw):
        split_point = m.start(1) + len(m.group(1))
        segment = raw[last_end:split_point].strip()
        tail = raw[m.end() :].strip()
        if _is_corporate_author_name(segment) and _is_corporate_author_name(tail):
            splits.append(segment)
            last_end = m.end()
    if splits:
        remaining = raw[last_end:].strip()
        if remaining:
            splits.append(remaining)
        return splits
    return [raw]


def _split_authors(raw: str) -> list[str]:
    raw = _HYPHENATED_NAME_BREAK.sub("-", raw).replace("⁠", "")
    raw = re.sub(r"\bet\s+al\.?", "", raw, flags=re.IGNORECASE).strip().rstrip(",&").strip()
    if not raw:
        return []
    if _is_corporate_author_name(raw) and "," not in raw:
        return _split_corporate_authors(raw)
    segments = re.split(r",\s*(?:and|&)\s*|,\s*", raw)
    out: list[str] = []
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        if _is_corporate_author_name(segment):
            out.extend(_split_corporate_authors(segment))
        else:
            for p in re.split(r"\s+and\s+|\s+&\s+", segment):
                p = p.strip()
                if p:
                    out.append(p)
    return out


def _looks_unfinished(text: str) -> bool:
    """True when a line was cut mid-sentence rather than ending one.

    A PDF text block is a physical line, not a sentence. "...reported by
    Haan (2025), with" is the whole block; the proposition continues on the
    next one (BUG-240 AC1).
    """
    stripped = text.rstrip()
    if not stripped:
        return False
    if stripped[-1] in ".!?:;":
        # A real terminator, unless it is an abbreviation's period.
        return stripped[-1] == "." and _closes_an_abbreviation(stripped + " ", len(stripped))
    return True


def merge_wrapped_lines(blocks: list[TextBlock]) -> list[TextBlock]:
    """Join consecutive blocks that are one wrapped sentence (BUG-240 AC1).

    Deliberately conservative on both axes:

    * only blocks with the **same page and paragraph** merge, so a merged
      block's anchor is never ambiguous and page/paragraph boundaries are
      preserved exactly as the acceptance criterion requires; and
    * only when the earlier block ends mid-sentence, so a heading followed
      by body text, or two complete sentences, are never glued into one
      false proposition.
    """
    tolerance = get_settings().citation_wrap_font_size_tolerance_pt
    merged: list[TextBlock] = []
    for block in blocks:
        text = block.text or ""
        if (
            merged
            and merged[-1].page == block.page
            and merged[-1].paragraph == block.paragraph
            and _looks_unfinished(merged[-1].text)
            and _same_typography(merged[-1], block, tolerance)
            and text.strip()
        ):
            previous = merged[-1]
            head = previous.text.rstrip()
            tail = text.lstrip()
            # BUG-240 AC4, hyphenated wrap: justification breaks a word at a
            # hyphen ("A neuro-" / "symbolic solver..."). Joining with a space
            # would put one INSIDE the word and corrupt both the graded claim
            # and the excerpt the instructor has to find in the PDF.
            #
            # The hyphen is kept rather than dissolved. "experi-" + "mental"
            # really is "experimental", but the excerpt has to be findable in
            # the source, where the hyphen is what is printed. Keeping it
            # preserves the exact characters; removing it would invent a
            # spelling that appears nowhere on the page. The cost is that a
            # purely typographic break stays hyphenated in the excerpt, which
            # is a readability wart and not a correctness claim.
            separator = "" if _HYPHEN_BREAK.search(head) else " "
            merged[-1] = previous.model_copy(update={"text": f"{head}{separator}{tail}"})
            continue
        merged.append(block)
    return merged


def _same_typography(previous: TextBlock, block: TextBlock, tolerance: float) -> bool:
    """A heading has no terminal punctuation but is not a wrapped line.

    PDF headings share a page (and no paragraph) with the body text after
    them, so without this check "Review of Related Literature" was glued to
    the next sentence's citation ("Review of Related Literature Gurmu et al.
    (2025)"), inventing a multiword author and an ambiguous match.
    """
    if (
        previous.max_font_size
        and block.max_font_size
        and abs(previous.max_font_size - block.max_font_size) > tolerance
    ):
        return False
    return not (previous.bold_ratio >= 0.5 and block.bold_ratio < 0.5)


def _anchor_for(block: TextBlock) -> str:
    if block.page is not None:
        return f"p. {block.page}"
    if block.paragraph is not None:
        return f"¶{block.paragraph}"
    return "document"


def _closes_an_abbreviation(text: str, boundary: int) -> bool:
    """True when the period just before `boundary` ends an abbreviation.

    `boundary` is the index of the whitespace that follows the period, so
    `text[:boundary]` ends with that period (BUG-240).
    """
    match = _TRAILING_TOKEN.search(text[:boundary])
    if match is None:
        return False
    token = match.group(1).rstrip(".")
    if len(token) == 1 and token.isalpha():
        # An author initial: "Smith, J. (2020) reported X" is one sentence.
        return True
    return token.lower() in _ABBREVIATIONS


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for b in _SENTENCE_BOUNDARY.finditer(text):
        if _closes_an_abbreviation(text, b.start()):
            continue  # "et al.", "e.g.", "J." -- not a sentence end
        spans.append((start, b.start()))
        start = b.end()
    spans.append((start, len(text)))
    return spans


def _sentence_at(
    text: str, pos: int, citation_end: int, spans: list[tuple[int, int]], *, max_chars: int
) -> tuple[str, bool]:
    """Keep an exact source substring without copying a whole long paragraph."""
    if not spans:
        return "", False
    index = max(0, bisect_right(spans, (pos, len(text) + 1)) - 1)
    start, end = spans[index]
    if end - start <= max_chars:
        return text[start:end].strip(), False
    window_start = max(start, pos - max_chars // 2)
    window_end = min(end, window_start + max_chars)
    if citation_end > window_end:
        window_end = min(end, citation_end)
        window_start = max(start, window_end - max_chars)
    return text[window_start:window_end].strip(), True


# --- cross-match ---------------------------------------------------------------

from app.checks.citations.cross_match import (  # noqa: E402, I001
    _first_reference_surname as _first_reference_surname,
    _reference_surnames as _reference_surnames,
    cross_match as cross_match,
    get_author_aliases as get_author_aliases,
)

# --- flag-ready output -----------------------------------------------------------


def orphan_flags(orphans: list[InTextCitation]) -> list[CitationFlagDraft]:
    return [
        CitationFlagDraft(
            severity=FlagSeverity.low,
            evidence_excerpt=cite.claim_sentence or cite.raw_text,
            page_anchor=cite.anchor,
            detail={
                "kind": "orphan_in_text_citation",
                "reason": ORPHAN_WORDING.format(authors=", ".join(cite.surnames), year=cite.year),
                "raw_text": cite.raw_text,
                "surnames": cite.surnames,
                "year": cite.year,
                "claim_excerpt_cropped": cite.claim_excerpt_cropped,
                # BUG-237/BUG-251 AC5: one in-text citation with no reference
                # entry is ONE missing reference, however many times it is
                # cited. Keyed on the cited work's own identity, never on the
                # claim sentence, and never on the anchor: the page is the
                # location. Every occurrence stays flagged and reachable.
                FINDING_GROUP_FIELD: finding_group_fingerprint(
                    "citation_integrity",
                    "orphan_in_text_citation",
                    "|".join(cite.surnames),
                    cite.year or "",
                ),
            },
        )
        for cite in orphans
    ]


NUMERIC_ORPHAN_WORDING = (
    "In-text citation {mark} points to source [{number}], but the numbered "
    "reference list ends at [{last}]. Possible missing reference or a numbering "
    "error. Please check manually."
)
NUMERIC_UNCITED_WORDING = (
    "Reference list entry [{number}] does not appear to be cited anywhere in "
    "the body text. Possible unused reference, or a citation form this checker "
    "did not recognize. Please check manually."
)


def numeric_linkage_flags(
    orphan_marks: list, uncited: list[ReferenceLike], n_entries: int
) -> list[CitationFlagDraft]:
    """Findings for a numbered list whose printed inventory is complete.

    `orphan_marks` and `uncited` come from `analyze_numbered_citations`,
    which already restricts both to strong citation context and withholds
    absence claims when any mark is uncertain. One missing number is one
    finding, however many marks cite it.
    """
    flags: list[CitationFlagDraft] = []
    seen: set[int] = set()
    for mark in orphan_marks:
        for number in mark.numbers or ():
            if number <= n_entries or number in seen:
                continue
            seen.add(number)
            flags.append(
                CitationFlagDraft(
                    severity=FlagSeverity.low,
                    evidence_excerpt=mark.claim_sentence or mark.raw_text,
                    page_anchor=mark.anchor,
                    detail={
                        "kind": "orphan_in_text_citation",
                        "reason": NUMERIC_ORPHAN_WORDING.format(
                            mark=mark.raw_text, number=number, last=n_entries
                        ),
                        "raw_text": mark.raw_text,
                        "number": number,
                        "claim_excerpt_cropped": mark.excerpt_cropped,
                        FINDING_GROUP_FIELD: finding_group_fingerprint(
                            "citation_integrity", "orphan_in_text_citation", "numeric", str(number)
                        ),
                    },
                )
            )
    for ref in uncited:
        label = reference_anchor(ref.raw_text, ref.order_index).removeprefix("reference #")
        flags.append(
            CitationFlagDraft(
                severity=FlagSeverity.low,
                evidence_excerpt=ref.raw_text,
                page_anchor=reference_anchor(ref.raw_text, ref.order_index),
                detail={
                    "kind": "uncited_reference",
                    "reason": NUMERIC_UNCITED_WORDING.format(number=label),
                    "order_index": ref.order_index,
                    "number": int(label) if label.isdigit() else None,
                },
            )
        )
    return flags


def uncited_flags(uncited: list[ReferenceLike]) -> list[CitationFlagDraft]:
    return [
        CitationFlagDraft(
            severity=FlagSeverity.low,
            evidence_excerpt=ref.raw_text,
            page_anchor=reference_anchor(ref.raw_text, ref.order_index),
            detail={
                "kind": "uncited_reference",
                "reason": UNCITED_WORDING.format(
                    first_author=(ref.authors or ["Unknown"])[0], year=ref.year
                ),
                "order_index": ref.order_index,
            },
        )
        for ref in uncited
    ]
