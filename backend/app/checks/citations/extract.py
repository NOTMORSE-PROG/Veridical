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
from dataclasses import dataclass
from typing import Any, Protocol

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

# A small, explicit set of lowercase connectors allowed INSIDE a multi-word
# name/phrase ("Commission on Higher Education", "de la Cruz") — bounded on
# purpose (ground rule 7 spirit: no unbounded "any lowercase word" escape
# hatch that would let prose bleed into an author capture the way `\w+`
# continuation did before this fix).
_CONNECTOR = r"(?:of|on|for|and|de|del|dela|van|von|der|the)"
_NAME_WORD = r"[A-ZÀ-Þ][\w'’.\-]*"
_NAME_PHRASE = rf"{_NAME_WORD}(?:\s(?:{_CONNECTOR}\s)?{_NAME_WORD})*"

# One author-list segment: "Damoco", "Damoco et al.", "Smith & Jones",
# "Smith, Jones, & Lee", "Smith and Jones", "Commission on Higher Education",
# "Vasquez Ferrer" (two-word surname, no connector needed).
_AUTHOR_SEGMENT = rf"{_NAME_PHRASE}(?:[\s,&]+(?:and\s+)?{_NAME_PHRASE})*(?:\s+et\s+al\.?)?"

# A whole parenthetical citation group: everything inside one set of parens
# that contains a 4-digit year (optionally letter-suffixed). The content
# (not the parens) is captured so it can be split on ';' for multi-cites.
_PAREN_GROUP = re.compile(r"\(([^()]*\d{4}[a-z]?[^()]*)\)")

# One citation within a parenthetical group (post ';' split): "Author(s),
# Year" with an optional page/paragraph locator and/or secondary-source tail.
_PAREN_ENTRY = re.compile(
    rf"^(?P<authors>{_AUTHOR_SEGMENT}),\s*(?P<year>\d{{4}}[a-z]?)"
    r"(?:,\s*(?i:pp?\.?|para\.?|section)\s*[\w\-–]+)?"
    r"(?:,\s*(?i:as\s+cited\s+in)\s+.+)?\s*$"
)

# Narrative citation: the author sits outside the parens, only the year
# (optionally letter-suffixed, optionally with a secondary-source tail) is
# inside — "Damoco et al. (2024)" / "Damoco (1990, as cited in Serquiña, 2025)".
_NARRATIVE = re.compile(
    rf"(?P<authors>{_AUTHOR_SEGMENT})\s\((?P<year>\d{{4}}[a-z]?)"
    r"(?:,\s*(?i:as\s+cited\s+in)\s+[^)]*)?\)"
)

# Same convention as V-006's `_PART_SPLIT`: a period/!/? followed by
# whitespace and a capital/digit/opening-quote starts a new sentence.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")

_LOOKBACK_CHARS = 40  # window checked before a narrative match for a
# preceding "as cited in" (the "As cited in Jones (2020), ..." phrasing).


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


@dataclass(frozen=True)
class CrossMatchResult:
    in_text: list[InTextCitation]
    linked: list[tuple[InTextCitation, list[ReferenceLike]]]
    orphans: list[InTextCitation]  # in-text citations with no matching reference
    uncited: list[ReferenceLike]  # reference entries never cited in-text


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


def extract_in_text_citations(blocks: list[TextBlock]) -> list[InTextCitation]:
    """Scan `blocks` (already filtered to exclude the reference-list span
    and furniture — see `app.ingest.references.non_reference_blocks`) for
    APA in-text citation shapes."""
    out: list[InTextCitation] = []
    for block in blocks:
        text = block.text
        if not text:
            continue
        spans = _sentence_spans(text)
        anchor = _anchor_for(block)

        for group_m in _PAREN_GROUP.finditer(text):
            for raw_entry in group_m.group(1).split(";"):
                raw_entry = raw_entry.strip()
                if not raw_entry:
                    continue
                entry_m = _PAREN_ENTRY.match(raw_entry)
                if not entry_m:
                    continue
                surnames = _split_authors(entry_m.group("authors"))
                if not surnames:
                    continue
                out.append(
                    InTextCitation(
                        surnames=surnames,
                        year=entry_m.group("year"),
                        is_narrative=False,
                        is_secondary=bool(_SECONDARY_MARK.search(raw_entry)),
                        raw_text=group_m.group(0),
                        anchor=anchor,
                        page=block.page,
                        paragraph=block.paragraph,
                        claim_sentence=_sentence_at(text, group_m.start(), spans),
                    )
                )

        for m in _NARRATIVE.finditer(text):
            surnames = _split_authors(m.group("authors"))
            if not surnames:
                continue
            preceding = text[max(0, m.start() - _LOOKBACK_CHARS) : m.start()]
            is_secondary = bool(_SECONDARY_MARK.search(m.group(0))) or bool(
                _SECONDARY_MARK.search(preceding)
            )
            out.append(
                InTextCitation(
                    surnames=surnames,
                    year=m.group("year"),
                    is_narrative=True,
                    is_secondary=is_secondary,
                    raw_text=m.group(0),
                    anchor=anchor,
                    page=block.page,
                    paragraph=block.paragraph,
                    claim_sentence=_sentence_at(text, m.start(), spans),
                )
            )
    return out


def _split_authors(raw: str) -> list[str]:
    raw = re.sub(r"\bet\s+al\.?", "", raw, flags=re.IGNORECASE).strip().rstrip(",&").strip()
    parts = re.split(r",\s*&\s*|,\s+and\s+|\s+&\s+|\s+and\s+|,\s*", raw)
    return [p.strip() for p in parts if p.strip()]


def _anchor_for(block: TextBlock) -> str:
    if block.page is not None:
        return f"p. {block.page}"
    if block.paragraph is not None:
        return f"¶{block.paragraph}"
    return "document"


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for b in _SENTENCE_BOUNDARY.finditer(text):
        spans.append((start, b.start()))
        start = b.end()
    spans.append((start, len(text)))
    return spans


def _sentence_at(text: str, pos: int, spans: list[tuple[int, int]]) -> str:
    for s, e in spans:
        if pos < e:
            return text[s:e].strip()
    return text[spans[-1][0] :].strip() if spans else text.strip()


# --- cross-match ---------------------------------------------------------------


def _reference_surnames(ref: ReferenceLike) -> set[str]:
    out: set[str] = set()
    for author in ref.authors or []:
        surname = author.split(",", 1)[0].strip() if "," in author else author.strip()
        if surname:
            out.add(surname.casefold())
    return out


def cross_match(in_text: list[InTextCitation], references: list[ReferenceLike]) -> CrossMatchResult:
    index: dict[str, list[ReferenceLike]] = {}
    for ref in references:
        for surname in _reference_surnames(ref):
            index.setdefault(surname, []).append(ref)

    linked: list[tuple[InTextCitation, list[ReferenceLike]]] = []
    orphans: list[InTextCitation] = []
    cited_order_indexes: set[int] = set()

    for cite in in_text:
        if cite.is_secondary:
            continue  # edge case: don't resolve the inner source
        candidates: list[ReferenceLike] = []
        seen_ids: set[int] = set()
        for surname in cite.surnames:
            for ref in index.get(surname.casefold(), []):
                if ref.order_index not in seen_ids:
                    seen_ids.add(ref.order_index)
                    candidates.append(ref)
        year_digits = cite.year[:4]
        matches = [c for c in candidates if str(c.year) == year_digits]
        if len(matches) > 1 and cite.year[-1].isalpha():
            # Same-author-same-year disambiguation (2024a/b): V-006's parser
            # strips the letter suffix from the stored `year` int but keeps
            # it in `raw_text` — use that to narrow down, if it narrows at all.
            refined = [c for c in matches if cite.year in (c.raw_text or "")]
            if refined:
                matches = refined
        if matches:
            cited_order_indexes.update(c.order_index for c in matches)
            linked.append((cite, matches))
        else:
            orphans.append(cite)

    uncited = [r for r in references if r.order_index not in cited_order_indexes]
    return CrossMatchResult(in_text=in_text, linked=linked, orphans=orphans, uncited=uncited)


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
            },
        )
        for cite in orphans
    ]


def uncited_flags(uncited: list[ReferenceLike]) -> list[CitationFlagDraft]:
    return [
        CitationFlagDraft(
            severity=FlagSeverity.low,
            evidence_excerpt=ref.raw_text,
            page_anchor="reference list",
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
