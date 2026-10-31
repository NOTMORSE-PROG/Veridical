"""F5 absence claims require supported identity and citation-style coverage.

Linkage findings come in two kinds, and each can only be withheld by what
could actually change it:

* An orphan ("this citation has no entry") is wrong only if some entry is in
  fact its source. An entry the reference reader could not fully parse is a
  possible source when its text could carry the cited name and year.
* An uncited entry ("nothing in the text cites this") is wrong only if some
  mark in the text names it. A citation the in-text scan could not read is a
  possible mark for the entries whose names appear in its paragraph, and a
  name printed beside the entry's year is a possible mark in a style the
  scanner did not recognise.

Everything withheld is returned with its reason so the check can count it as
unassessed. Before BUG-590, one unreadable line withheld every linkage
finding in the document.
"""

import re
import unicodedata
from dataclasses import dataclass

from app.checks.citations.cross_match import get_author_aliases
from app.checks.citations.extract import ScanGapSite, merge_wrapped_lines
from app.ingest.citation_dates import citation_date_key, date_patterns, reference_date_key
from app.ingest.reference_metadata_patterns import load_reference_metadata_patterns
from app.models.enums import CitationParseStatus

# Reasons recorded on withheld linkage findings.
WITHHELD_POSSIBLE_SOURCE = "unparsed_entry_may_be_source"
WITHHELD_ENTRY_TEXT_NAMES_IT = "entry_text_names_citation"
WITHHELD_UNREAD_CITATION = "unread_citation_may_name_entry"
WITHHELD_UNRECOGNIZED_MENTION = "possible_unrecognized_citation"
WITHHELD_POSSIBLE_TARGET = "possible_source_of_unmatched_citation"
WITHHELD_SCAN_INCOMPLETE = "citation_scan_incomplete"

# The date key `citation_dates` gives an explicit "n.d." citation or entry.
NO_DATE_KEY = citation_date_key("n.d.")
# Hyphens (ASCII, Unicode hyphen, non-breaking hyphen, figure dash, en dash)
# and apostrophes inside a word.
_JOINER = re.compile(r"(?<=\w)[-\u2010\u2011\u2012\u2013'\u2019]\s*(?=\w)")
_NON_WORD = re.compile(r"[\W_]+")


def guarded_absence_candidates(candidates, references, body_blocks, settings):
    patterns = load_reference_metadata_patterns(settings.reference_metadata_patterns_file)
    mentions = []
    for block in merge_wrapped_lines(body_blocks):
        for match in patterns.standard_identifier.finditer(block.text):
            mentions.append((match, block))
    leads = []
    possible = set()
    for reference in references:
        for listed in patterns.standard_identifier.finditer(reference.raw_text):
            for mention, block in mentions:
                if (listed.group("series"), listed.group("number")) != (
                    mention.group("series"),
                    mention.group("number"),
                ):
                    continue
                edition = mention.group("edition")
                listed_edition = listed.group("edition") or str(reference.year or "")
                if edition and edition != listed_edition:
                    continue
                possible.add(reference.order_index)
                leads.append(
                    {
                        "reference_order_index": reference.order_index,
                        "raw_mark": mention.group(0),
                        "page": block.page,
                        "paragraph": block.paragraph,
                        "status": "possible_identifier_link",
                        "reason": (
                            "A matching standard identifier is mentioned; edition and "
                            "claim support are unassessed."
                        ),
                    }
                )
                break
            if reference.order_index in possible:
                break
    unsupported = {
        reference.order_index for reference in references if not metadata_usable(reference)
    }
    unassessed = possible | unsupported
    return ([ref for ref in candidates if ref.order_index not in unassessed], unassessed, leads)


def metadata_usable(reference) -> bool:
    """The entry was parsed with an author and a date, so the matcher could link it."""
    return bool(
        reference.parse_status == CitationParseStatus.parsed
        and reference.authors
        and reference_date_key(reference) is not None
    )


# --- name and year matching -----------------------------------------------------------


class _Folded:
    """Text in the forms names are compared in: accents and case removed, and
    punctuation read as a word break, as nothing inside a hyphenated or
    apostrophised word ("Xiong-Bin" is also "xiongbin"), and as nothing
    between the letters of a dotted or spaced initialism ("D.T.I." and
    "W. H. O." are also "dti" and "who")."""

    def __init__(self, text: str):
        plain = "".join(
            ch for ch in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(ch)
        ).casefold()
        spaced = _NON_WORD.sub(" ", plain).strip()
        joined = _NON_WORD.sub(" ", _JOINER.sub("", plain)).strip()
        self.forms = tuple(
            f" {form} " for form in dict.fromkeys((spaced, joined, _join_initials(spaced)))
        )

    def has(self, keys) -> bool:
        return any(f" {key} " in form for key in keys for form in self.forms)


def _join_initials(form: str) -> str:
    out: list[str] = []
    run: list[str] = []
    for word in [*form.split(), ""]:
        if len(word) == 1 and word.isalpha():
            run.append(word)
            continue
        if run:
            out.extend(["".join(run)] if len(run) > 1 else run)
            run = []
        if word:
            out.append(word)
    return " ".join(out)


@dataclass(frozen=True)
class _NameKeys:
    phrases: frozenset[str]  # a whole name or a known alias of it
    words: frozenset[str]  # single distinctive words of the name


def _name_keys(names, settings) -> _NameKeys:
    patterns = load_reference_metadata_patterns(settings.reference_metadata_patterns_file)
    generic = (
        patterns.corporate_author_words
        | patterns.author_institution_words
        | patterns.name_particles
    )
    phrases, words = set(), set()
    for name in names:
        for alias in {name, *get_author_aliases(name)}:
            for form in _Folded(alias).forms:
                form = form.strip()
                if not form:
                    continue
                phrases.add(form)
                words.update(
                    word
                    for word in form.split()
                    if len(word) >= settings.citation_linkage_name_word_min_chars
                    and word not in generic
                    and not word.isdigit()
                )
    return _NameKeys(frozenset(phrases), frozenset(words))


def _surnames(reference) -> list[str]:
    out = []
    for author in reference.authors or []:
        surname = author.split(",", 1)[0] if "," in author else author
        if surname.strip():
            out.append(surname.strip())
    return out


def _date_tokens(text: str, settings) -> list[re.Match]:
    """Every date the configured conventions can read in `text` (a four-digit
    year or a no-date token such as "n.d."), not counting digits inside a
    longer number."""
    return [
        match
        for match in date_patterns(settings)["group"].finditer(text or "")
        if not (match.start() and text[match.start() - 1].isdigit())
        and not (match.end() < len(text) and text[match.end()].isdigit())
    ]


def _years(text: str, settings) -> set[str]:
    return {m.group(0)[:4] for m in _date_tokens(text, settings) if m.group(0)[:4].isdigit()}


def _printed_date(raw: str, settings) -> re.Match | None:
    """The entry's own date as printed after its authors, e.g. "(2023)"."""
    return date_patterns(settings)["reference"].search(raw or "")


def _author_zone(raw: str, settings) -> str:
    """The part of an entry before its date, where the author is printed."""
    printed = _printed_date(raw, settings)
    if printed:
        return raw[: printed.start()]
    tokens = _date_tokens(raw, settings)
    return raw[: tokens[0].start()] if tokens else raw


# --- orphans ----------------------------------------------------------------------------


@dataclass(frozen=True)
class WithheldOrphan:
    citation: object
    reason: str
    possible_reference_order_indexes: tuple[int, ...]


@dataclass(frozen=True)
class _Entry:
    reference: object
    text: _Folded
    zone: _Folded
    aliases: frozenset[str]
    years: frozenset[str]
    undated: bool
    usable: bool


def _entry(reference, settings) -> _Entry:
    raw = reference.raw_text or ""
    printed = _printed_date(raw, settings)
    printed_year = printed.group(1)[:4] if printed and printed.group(1)[:4].isdigit() else None
    return _Entry(
        reference=reference,
        text=_Folded(raw),
        zone=_Folded(_author_zone(raw, settings)),
        aliases=_name_keys(_surnames(reference), settings).phrases,
        years=frozenset(_years(raw, settings)),
        # No printed year: the entry is dated "n.d." or its date could not
        # be read, so a citation of it may give any year.
        undated=printed_year is None and reference_date_key(reference) in (None, NO_DATE_KEY),
        usable=metadata_usable(reference),
    )


def reportable_orphans(orphans, references, settings):
    """Split orphan citations into those no entry could be the source of, and
    those withheld because some entry might be.

    An entry is a possible source when it could carry the cited name and the
    cited year. The name counts when the entry's text prints it (or a short
    form of it), a distinctive word of it sits in the entry's author part, or
    the cited name is a short form of the entry's author. The year counts
    when it appears anywhere in the entry. For an entry the reader could not
    fully parse, a missing author means any name is possible, and a missing
    or "n.d." date means any year is.
    """
    entries = [_entry(reference, settings) for reference in references]
    kept, withheld = [], []
    for cite in orphans:
        keys = _name_keys(cite.surnames, settings)
        year = citation_date_key(cite.year or "")
        sources, reason = [], WITHHELD_ENTRY_TEXT_NAMES_IT
        for entry in entries:
            named = (
                entry.text.has(keys.phrases)
                or entry.zone.has(keys.words)
                or bool(keys.phrases & entry.aliases)
            )
            dated = year in entry.years if year != NO_DATE_KEY else entry.undated
            if not entry.usable:
                named = named or not entry.reference.authors
                dated = dated or entry.undated
            if named and dated:
                sources.append(entry.reference.order_index)
                if not entry.usable:
                    reason = WITHHELD_POSSIBLE_SOURCE
        if sources:
            withheld.append(WithheldOrphan(cite, reason, tuple(sources)))
        else:
            kept.append(cite)
    return kept, withheld


# --- uncited entries --------------------------------------------------------------------


def scoped_absence_candidates(
    candidates, body_blocks, gap_sites, gaps, settings, possible_sources=frozenset()
):
    """Absence candidates no unread or unrecognised mark could be citing.

    Returns the kept candidates and {order_index: reason} for the withheld.
    A gap that covers the whole document (the scan stopped early), or any gap
    reason with no recorded paragraph, still withholds every candidate.
    `possible_sources` are entries a withheld orphan might be citing: saying
    nothing cites them would contradict keeping that orphan quiet.
    """
    sited = {reason for site in gap_sites if site.text is not None for reason in site.reasons}
    if any(site.text is None for site in gap_sites) or set(gaps) - sited:
        return [], {ref.order_index: WITHHELD_SCAN_INCOMPLETE for ref in candidates}
    unread = [_Folded(site.text) for site in gap_sites]
    blocks = [
        (_Folded(block.text), _years(block.text, settings))
        for block in merge_wrapped_lines(body_blocks)
    ]
    kept, withheld = [], {}
    for reference in candidates:
        keys = _name_keys(_surnames(reference), settings)
        names = keys.phrases | keys.words
        year = reference_date_key(reference)
        year = year[:4] if year and year[:4].isdigit() else None
        if reference.order_index in possible_sources:
            withheld[reference.order_index] = WITHHELD_POSSIBLE_TARGET
        elif any(site.has(names) for site in unread):
            withheld[reference.order_index] = WITHHELD_UNREAD_CITATION
        elif year and any(year in years and text.has(names) for text, years in blocks):
            withheld[reference.order_index] = WITHHELD_UNRECOGNIZED_MENTION
        else:
            kept.append(reference)
    return kept, withheld


def unread_sites_detail(gap_sites: list[ScanGapSite]) -> list[dict]:
    """Where the unread citations are, for the audit record and the report."""
    return [
        {
            "page": site.page,
            "paragraph": site.paragraph,
            "reasons": sorted(site.reasons),
            "scope": "document" if site.text is None else "paragraph",
        }
        for site in gap_sites
    ]
