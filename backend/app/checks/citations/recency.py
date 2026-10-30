"""Citation recency note (F5, V-092).

Advisers often ask that related literature be recent, commonly within five
years of the manuscript. This module reports which reference-list sources
fall outside that window. It does not judge whether an older source is
justified; a foundational work can be exactly right, and the instructor
dismisses those notes with a reason.

Two rules keep the note honest:

- A source is only called older than the window when its publication year
  can be read with confidence. An entry with no date, an "in press" marker,
  a year that conflicts with another date in the entry, or text the reader
  cannot place is counted as "year not readable" and never flagged.
- The reference year is the manuscript's own year when its title page
  states exactly one, otherwise the date of the check. The summary always
  says which one was used.

The window, the reference-year basis and whether these notes count toward
readiness are per-format settings (`Rubric.citation_recency`) with defaults
from configuration. The reading grammar (undated markers, month names,
access-note prefixes) is data in `data/citation_recency.json`.

This module reads the parser's saved fields and raw entry text only. It never
re-segments the reference list.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.checks.citations.extract import CitationFlagDraft
from app.config import Settings
from app.ingest.schemas import ExtractionResult
from app.ingest.titlepage import front_matter_blocks
from app.models.enums import CitationParseStatus, FlagSeverity

SUMMARY_SCHEMA_VERSION = "citation-recency-v1"
FINDING_KIND = "outside_recency_window"
ReferenceYearBasis = Literal["manuscript_year", "check_date"]

_DEFAULT_GRAMMAR_FILE = Path(__file__).parent / "data" / "citation_recency.json"


# ---------------------------------------------------------------------------
# Settings: per format, with configuration defaults


class CitationRecencyFormatSettings(BaseModel):
    """What a format may set. Every field is optional; a missing field uses
    the configured default, so an instructor only states what differs."""

    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool | None = None
    window_years: int | None = Field(default=None, ge=1, le=100)
    reference_year_basis: ReferenceYearBasis | None = None
    counts_toward_readiness: bool | None = None


@dataclass(frozen=True)
class RecencyPolicy:
    enabled: bool
    window_years: int
    reference_year_basis: ReferenceYearBasis
    counts_toward_readiness: bool
    # Where each value came from: "format" or "default".
    sources: dict[str, str]

    def record(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "window_years": self.window_years,
            "reference_year_basis": self.reference_year_basis,
            "counts_toward_readiness": self.counts_toward_readiness,
            "setting_sources": dict(self.sources),
        }


_SETTING_DEFAULTS = {
    "enabled": "citation_recency_enabled",
    "window_years": "citation_recency_window_years",
    "reference_year_basis": "citation_recency_reference_year_basis",
    "counts_toward_readiness": "citation_recency_counts_toward_readiness",
}


def resolve_policy(stored: Any, settings: Settings) -> RecencyPolicy:
    """Raises ValueError when the format's saved setting is not valid.

    An invalid saved setting is never quietly replaced by the defaults: the
    instructor chose something, and a window they did not choose would put
    notes on the report under their name."""
    if stored is None:
        chosen = CitationRecencyFormatSettings()
    else:
        try:
            chosen = CitationRecencyFormatSettings.model_validate(stored)
        except ValidationError as exc:
            raise ValueError("citation recency format setting is not valid") from exc
    values, sources = {}, {}
    for name, setting_name in _SETTING_DEFAULTS.items():
        value = getattr(chosen, name)
        if value is None:
            values[name] = getattr(settings, setting_name)
            sources[name] = "default"
        else:
            values[name] = value
            sources[name] = "format"
    return RecencyPolicy(**values, sources=sources)


def effective_settings(stored: Any, settings: Settings) -> dict[str, Any]:
    """The values a format's next check will use, for the rubric screens."""
    return resolve_policy(stored, settings).record()


# ---------------------------------------------------------------------------
# Grammar data


@dataclass(frozen=True)
class RecencyGrammar:
    version: str
    sha256: str
    earliest_year: int
    latest_year: int
    undated: re.Pattern[str]
    access_note: re.Pattern[str]
    access_note_date_span: int
    year_or_range: str
    standalone_year: re.Pattern[str]
    date_positions: tuple[re.Pattern[str], ...]
    title_page_date_line: re.Pattern[str]


class _GrammarData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: str = Field(min_length=1)
    earliest_plausible_year: int = Field(ge=1000, le=2999)
    latest_plausible_year: int = Field(ge=1000, le=2999)
    undated_markers: list[str] = Field(min_length=1)
    locator_abbreviations: list[str] = Field(default_factory=list)
    access_note_date_span_chars: int = Field(ge=0, le=500)
    date_prefixes: list[str] = Field(default_factory=list)
    range_separators: str = Field(min_length=1)
    access_note_prefixes: list[str] = Field(min_length=1)
    months: list[str] = Field(min_length=1)


def _alternatives(words: list[str]) -> str:
    # Longest first so "Sept" wins over "Sep" and "Last accessed" over "accessed".
    ordered = sorted({w.strip() for w in words if w.strip()}, key=len, reverse=True)
    return "|".join(re.sub(r"(\\ )+", r"\\s+", re.escape(word)) for word in ordered)


@lru_cache
def load_grammar(path: Path | None = None) -> RecencyGrammar:
    content = (path or _DEFAULT_GRAMMAR_FILE).read_bytes()
    raw = _GrammarData.model_validate(json.loads(content))
    if raw.earliest_plausible_year >= raw.latest_plausible_year:
        raise ValueError("Citation recency plausible year bounds are reversed")
    months = _alternatives(raw.months)
    seps = re.escape(raw.range_separators)
    # A four-digit run that is not part of a longer number, an identifier
    # ("ISO 9001:2015", "abs/2012.04567") or a hyphenated code ("55-2020").
    year = r"(?<![\w./:\\" + seps + r"])\d{4}(?:[a-z](?![a-z]))?(?![\w]|[.,:/]\d)"
    range_token = (
        r"(?P<range>(?<![\w./:\\" + seps + r"])(?P<start>\d{4})[a-z]?\s*[" + seps + r"]\s*"
        r"(?P<end>\d{4}|\d{2})[a-z]?(?![\w]|[.,:/]\d))"
    )
    year_or_range = rf"(?:{range_token}|(?P<single>{year}))"
    end = r"(?=[.,;:)\]\s]|$)"
    month_day = rf"(?:(?:{months})\.?,?\s+(?:\d{{1,2}}(?:st|nd|rd|th)?,?\s+)?)"
    # "pp. 1541-1544." is a page range, not an author-date year.
    not_locator = "".join(
        # Case-sensitive, so an author initial ("J. P. 2019.") is not a page.
        rf"(?<!\b(?-i:{re.escape(word)})\s)"
        for word in sorted(set(raw.locator_abbreviations))
    )
    positions = [
        # "(2019)", "(2019a)", "(2019, May 3)", "(May 2019)", "(2019-2020)"
        rf"\(\s*{month_day}?{year_or_range}\s*(?:,[^()]*)?\)",
        # ", 2019." as in IEEE and many journal styles; "May 2019"
        rf"(?:,\s*|\b{month_day}){year_or_range}{end}",
        # "Smith, J. 2019. Title" (author-date without parentheses)
        rf"(?<=\.\s){not_locator}{year_or_range}\.(?=\s|$)",
    ]
    if raw.date_prefixes:
        positions.append(rf"(?:{_alternatives(raw.date_prefixes)})\s*{year_or_range}{end}")
    access = _alternatives(raw.access_note_prefixes)
    initials = [m for m in raw.undated_markers if re.fullmatch(r"\w\.\s*\w\.", m.strip())]
    words = [m for m in raw.undated_markers if m not in initials]
    undated = []
    if words:
        undated.append(rf"(?<!\w)(?:{_alternatives(words)})(?!\w)")
    if initials:
        # Inside parentheses, or closing the entry.
        undated.append(rf"\(\s*(?:{_alternatives(initials)})")
        undated.append(rf"(?<!\w)(?:{_alternatives(initials)})\s*\.?\s*$")
    return RecencyGrammar(
        version=raw.version,
        sha256=hashlib.sha256(content).hexdigest(),
        earliest_year=raw.earliest_plausible_year,
        latest_year=raw.latest_plausible_year,
        undated=re.compile("|".join(undated), re.I),
        access_note=re.compile(
            rf"\b(?:{access})\b\s*(?::|on\b|from\b|at\b|(?=(?:{months})\.?\s+\d|\d))", re.I
        ),
        access_note_date_span=raw.access_note_date_span_chars,
        year_or_range=year_or_range,
        standalone_year=re.compile(year),
        date_positions=tuple(re.compile(pattern, re.I) for pattern in positions),
        title_page_date_line=re.compile(rf"^\s*{month_day}?(?P<year>\d{{4}})\.?\s*$", re.I),
    )


# ---------------------------------------------------------------------------
# Reading one entry's publication year

# Identifiers end before a closing period or comma, so "... 27625509. 2024."
# keeps the date that follows the link.
_URL = re.compile(r"(?:https?://|www\.)\S*?(?=[.,;)]?(?:\s|$))", re.I)
_DOI = re.compile(r"(?:\bdoi:\s*)?\b10\.\d{4,9}/\S*?(?=[.,;)]?(?:\s|$))", re.I)
_ARXIV = re.compile(r"\b(?:arxiv:\s*|abs/)\d{4}\.\d{4,5}(?:v\d+)?", re.I)
# Double-quoted spans hold titles in IEEE and similar styles. A year inside a
# title is the subject of the work, not its date. Single quotes are left
# alone: they double as apostrophes ("Children's") and cannot bound a span.
_QUOTED = re.compile(r"“[^”]*”|\"[^\"]*\"|«[^»]*»")

# A period closing a sentence: followed by space and a capital or an opening
# bracket. Initials ("J. P. Rizal") also match, which only ends a note early.
_SENTENCE_END = re.compile(r"\.\s+(?=[A-Z(\[])")

YearStatus = Literal["dated", "not_readable"]


@dataclass(frozen=True)
class YearReading:
    status: YearStatus
    year: int | None = None
    # As printed, e.g. "2019" or "2019-2020"; None when not readable.
    year_text: str | None = None
    reason: str | None = None


def _span_year(match: re.Match[str], grammar: RecencyGrammar) -> tuple[int, str] | None:
    """The latest year a date token stands for, plus how it was printed."""
    if match.group("single"):
        text = match.group("single")
        value = int(text[:4])
    else:
        start, end = match.group("start"), match.group("end")
        first = int(start)
        last = int(end) if len(end) == 4 else int(start[:2] + end)
        if last <= first:
            # "2019-18" or "1995-1990" are not ranges; read neither year.
            return None
        text = match.group("range")
        value = last
    if not grammar.earliest_year <= value <= grammar.latest_year:
        return None
    return value, text


def _strip_non_dates(raw: str, grammar: RecencyGrammar) -> str:
    text = _QUOTED.sub(" ", raw)
    for pattern in (_URL, _DOI, _ARXIV):
        text = pattern.sub(" ", text)
    # Access and retrieval notes carry the reading date, not the publication
    # date. A note runs through its own year when that year follows closely
    # within the same sentence ("Accessed: 2023-07-04. 2023." keeps the
    # final 2023); otherwise it runs to the end of its sentence, or of the
    # entry. What follows a note's sentence may be another merged entry.
    while access := grammar.access_note.search(text):
        boundary = _SENTENCE_END.search(text, access.end())
        sentence_end = boundary.start() + 1 if boundary else len(text)
        window_end = min(sentence_end, access.end() + grammar.access_note_date_span)
        own_year = re.search(r"\d{4}", text[access.end() : window_end])
        cut = access.end() + own_year.end() if own_year else sentence_end
        text = text[: access.start()] + " " + text[cut:]
    return text


def read_publication_year(
    raw_text: str,
    parsed_year: int | None,
    parse_status: CitationParseStatus | str,
    grammar: RecencyGrammar,
    check_year: int,
) -> YearReading:
    """Read the year a source was published, or say it cannot be read.

    Only years in a date position count: inside parentheses, after a comma
    or month, after an author-date period, or after a configured prefix. The
    entry must name exactly one such date. A different year later in the
    entry that is newer than that date (and not in the future) makes the
    entry ambiguous, so it is not read."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return YearReading("not_readable", reason="no_text")
    text = _strip_non_dates(raw_text, grammar)
    if grammar.undated.search(text):
        return YearReading("not_readable", reason="no_date")
    if str(parse_status) == CitationParseStatus.parsed.value and parsed_year is None:
        # The parser saw an explicit no-date token the grammar does not list.
        return YearReading("not_readable", reason="no_date")

    candidates: dict[int, str] = {}
    covered: list[tuple[int, int]] = []
    for pattern in grammar.date_positions:
        for match in pattern.finditer(text):
            token = _span_year(match, grammar)
            if token is None:
                continue
            value, printed = token
            candidates.setdefault(value, printed)
            covered.append(match.span())
    if not candidates:
        return YearReading("not_readable", reason="no_year")
    if len(candidates) > 1:
        return YearReading("not_readable", reason="conflicting_years")
    value, printed = next(iter(candidates.items()))

    # The parser's year must be the date found here (or the start of the
    # range it ends); anything else means the two readings disagree.
    if isinstance(parsed_year, int) and not printed.startswith(str(parsed_year)):
        return YearReading("not_readable", reason="conflicting_years")

    def inside(position: int) -> bool:
        return any(start <= position < end for start, end in covered)

    for other in grammar.standalone_year.finditer(text):
        if inside(other.start()):
            continue
        other_year = int(other.group(0)[:4])
        if other_year >= grammar.earliest_year and value < other_year <= check_year:
            return YearReading("not_readable", reason="conflicting_years")
    return YearReading("dated", year=value, year_text=printed)


# ---------------------------------------------------------------------------
# The manuscript's own year


@dataclass(frozen=True)
class ReferenceYear:
    year: int
    basis: ReferenceYearBasis
    evidence_text: str | None = None
    evidence_anchor: str | None = None
    # Why the manuscript year was not used, when the basis allowed it.
    fallback_reason: str | None = None


def _anchor(block) -> str:
    if block.page is not None:
        return f"p. {block.page}"
    if block.paragraph is not None:
        return f"¶{block.paragraph}"
    return "document"


def title_page_year(
    extraction: ExtractionResult | None, grammar: RecencyGrammar
) -> tuple[int | None, str | None, str | None, str | None]:
    """(year, text, anchor, issue). A line that is only a date ("May 2025",
    "September 2025", "2025") is read; a title page naming two different
    years is ambiguous and none is used."""
    if extraction is None:
        return None, None, None, "manuscript_year_not_found"
    found: dict[int, tuple[str, str]] = {}
    for block in front_matter_blocks(extraction):
        for line in block.text.splitlines():
            match = grammar.title_page_date_line.match(line)
            if not match:
                continue
            year = int(match.group("year"))
            if grammar.earliest_year <= year <= grammar.latest_year:
                found.setdefault(year, (line.strip(), _anchor(block)))
    if not found:
        return None, None, None, "manuscript_year_not_found"
    if len(found) > 1:
        return None, None, None, "manuscript_year_ambiguous"
    year, (text, anchor) = next(iter(found.items()))
    return year, text, anchor, None


def check_year_of(check_date: datetime, settings: Settings) -> int:
    return check_date.astimezone(ZoneInfo(settings.citation_recency_timezone)).year


def choose_reference_year(
    policy: RecencyPolicy,
    extraction: ExtractionResult | None,
    grammar: RecencyGrammar,
    check_year: int,
    newest_cited_year: int | None,
) -> ReferenceYear:
    if policy.reference_year_basis == "check_date":
        return ReferenceYear(check_year, "check_date")
    year, text, anchor, issue = title_page_year(extraction, grammar)
    if issue is None and year is not None:
        if year > check_year:
            issue = "manuscript_year_after_check_date"
        elif newest_cited_year is not None and newest_cited_year > year:
            # The manuscript cites work published after the year on its
            # title page, so that year is not when it was written.
            issue = "manuscript_year_precedes_cited_source"
    if issue is not None:
        return ReferenceYear(check_year, "check_date", fallback_reason=issue)
    return ReferenceYear(year, "manuscript_year", text, anchor)


# ---------------------------------------------------------------------------
# The assessment


_BASIS_PHRASE = {
    "manuscript_year": "the year on the manuscript's title page",
    "check_date": "the date of this check",
}


def outside_window_wording(year_text: str, policy: RecencyPolicy, reference: ReferenceYear) -> str:
    oldest = reference.year - policy.window_years
    window = (
        f"the {policy.window_years}-year window this format sets for related literature"
        if policy.sources.get("window_years") == "format"
        else f"the default {policy.window_years}-year window for related literature"
    )
    return (
        f"Published {year_text}, which is older than {window}. The window counts back from "
        f"{reference.year}, {_BASIS_PHRASE[reference.basis]}, so sources from {oldest} onward "
        "fall inside it. An older source can still be the right one to cite, for example a "
        "foundational work; if so, dismiss this note with a reason."
    )


@dataclass
class RecencyAssessment:
    summary: dict[str, Any]
    flags: list[CitationFlagDraft] = field(default_factory=list)


def unassessed_summary(reason: str, policy: RecencyPolicy | None = None) -> dict[str, Any]:
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "status": "unassessed",
        "unassessed_reason": reason,
        **(policy.record() if policy else {}),
    }


def off_summary(policy: RecencyPolicy) -> dict[str, Any]:
    """The format turned the note off; nothing was read or flagged."""
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "status": "off",
        "unassessed_reason": None,
        **policy.record(),
    }


def assess_recency(
    citations: list[Any],
    extraction: ExtractionResult | None,
    policy: RecencyPolicy,
    grammar: RecencyGrammar,
    check_year: int,
) -> RecencyAssessment:
    if not policy.enabled:
        return RecencyAssessment(off_summary(policy))
    readings = [
        (
            citation,
            read_publication_year(
                citation.raw_text, citation.year, citation.parse_status, grammar, check_year
            ),
        )
        for citation in citations
    ]
    newest = max(
        (r.year for _, r in readings if r.status == "dated" and r.year <= check_year),
        default=None,
    )
    reference = choose_reference_year(policy, extraction, grammar, check_year, newest)
    oldest = reference.year - policy.window_years

    sources, flags = [], []
    reasons: dict[str, int] = {}
    n_outside = n_within = n_after = 0
    for citation, reading in readings:
        if reading.status != "dated":
            reasons[reading.reason] = reasons.get(reading.reason, 0) + 1
            sources.append(
                {
                    "reference_order_index": citation.order_index,
                    "status": "year_not_readable",
                    "year": None,
                    "reason": reading.reason,
                }
            )
            continue
        outside = reading.year < oldest
        n_outside += outside
        n_within += not outside
        n_after += reading.year > reference.year
        sources.append(
            {
                "reference_order_index": citation.order_index,
                "status": "outside_window" if outside else "within_window",
                "year": reading.year,
                "year_text": reading.year_text,
                "reason": None,
            }
        )
        if not outside:
            continue
        detail: dict[str, Any] = {
            "kind": FINDING_KIND,
            "reason": outside_window_wording(reading.year_text, policy, reference),
            "order_index": citation.order_index,
            "authors": list(citation.authors or []),
            "year": reading.year,
            "year_text": reading.year_text,
            "reference_year": reference.year,
            "reference_year_basis": reference.basis,
            "window_years": policy.window_years,
            "oldest_year_in_window": oldest,
        }
        if not policy.counts_toward_readiness:
            detail["affects_readiness"] = False
        flags.append(
            CitationFlagDraft(
                severity=FlagSeverity.low,
                evidence_excerpt=citation.raw_text,
                page_anchor=f"reference #{citation.order_index + 1}",
                detail=detail,
            )
        )
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "status": "assessed",
        "unassessed_reason": None,
        **policy.record(),
        "grammar_version": grammar.version,
        "grammar_sha256": grammar.sha256,
        "reference_year": reference.year,
        "reference_year_basis": reference.basis,
        "reference_year_evidence": (
            {"text": reference.evidence_text, "anchor": reference.evidence_anchor}
            if reference.evidence_text
            else None
        ),
        "reference_year_fallback_reason": reference.fallback_reason,
        "check_year": check_year,
        "oldest_year_in_window": oldest,
        "n_references": len(citations),
        "n_dated": n_outside + n_within,
        "n_outside_window": n_outside,
        "n_within_window": n_within,
        "n_dated_after_reference_year": n_after,
        "n_year_not_readable": sum(reasons.values()),
        "year_not_readable_reasons": reasons,
        "sources": sources,
    }
    return RecencyAssessment(summary, flags)
