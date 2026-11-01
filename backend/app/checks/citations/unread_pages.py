"""Which uncited-reference claims a page left unread could change (BUG-619).

A page whose reading order is unresolved is left out of the checks (BUG-507),
but its text is still there, out of order. Before this, one such page withheld
every uncited claim in the thesis, even when the page was an appendix table
that named none of the entries.

An entry is now called uncited only when no unread page could be citing it,
judged with the same rules the readable text is read with: for an author-year
list, any of the entry's names or a known alias, compared the way the linkage
guard compares names (accents, case, hyphens and initialisms set aside); for a
numbered list, any bracket mark that cites its number, ranges and "[3]-[6]"
bridges included. An entry with no name to look for, or an unread page with a
bracket mark that cannot be read, falls back to where the citing text is: an
unread page among the pages that cite sources withholds the entry, and so does
a thesis whose citing pages could not be found. Whatever stays withheld is
shown as a lead that names the pages and what was found on them, and does not
change readiness.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.checks.citations.extract import CitationFlagDraft, ReferenceLike
from app.checks.citations.linkage_guard import has_name_keys, name_found_in
from app.checks.citations.numbered import (
    bracket_numbers_in_text,
    load_numeric_context,
    reference_anchor,
)
from app.config import Settings
from app.ingest.citation_dates import reference_date_key
from app.ingest.schemas import ExtractionResult
from app.models.enums import FlagSeverity

_DEFAULT_FILE = Path(__file__).parent / "data" / "unread_pages_linkage.json"


@lru_cache(maxsize=4)
def load_policy(path: Path | None = None) -> dict:
    return json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))


def _number(ref: ReferenceLike) -> int | None:
    label = reference_anchor(ref.raw_text, ref.order_index).removeprefix("reference #")
    return int(label) if label.isdigit() else None


# (entry, pages that could cite it, why, what was found there)
Withheld = tuple[ReferenceLike, list[int], str, str | None]


def split_by_unread_pages(
    candidates: list[ReferenceLike],
    extraction: ExtractionResult,
    *,
    numbered: bool,
    citing_pages: set[int],
    settings: Settings,
) -> tuple[list[ReferenceLike], list[Withheld]]:
    """The candidates no unread page could be citing, and the rest with the
    pages that could, why ("named", "unreadable_mark" or "within_citing_text"),
    and the name or
    number found."""
    unread = sorted(extraction.unread_pages or [])
    if not unread:
        return list(candidates), []
    texts = {page: (extraction.unread_text or {}).get(page, "") for page in unread}
    span = (min(citing_pages), max(citing_pages)) if citing_pages else None
    inside = [p for p in unread if span is None or span[0] <= p <= span[1]]
    marks: dict[int, tuple[set[int], bool]] = {}
    if numbered:
        context = load_numeric_context(settings.numeric_citation_context_file)
        marks = {
            page: bracket_numbers_in_text(
                text, max_range_span=settings.numeric_citation_max_range_span, context=context
            )
            for page, text in texts.items()
        }
    unreadable_pages = [p for p in unread if numbered and marks[p][1]]
    released, withheld = [], []
    for ref in candidates:
        found: str | None = None
        if numbered:
            number = _number(ref)
            named = [p for p in unread if number is not None and number in marks[p][0]]
            found = str(number) if named else None
            usable = number is not None
        else:
            named = []
            for page in unread:
                name = name_found_in(ref, texts[page], settings)
                if name:
                    named.append(page)
                    found = found or name
            usable = has_name_keys(ref, settings)
        if named:
            withheld.append((ref, named, "named", found))
        elif numbered and unreadable_pages:
            # On a readable page an unreadable citation mark leaves every entry
            # possibly cited; an unread page is given no more trust than that.
            withheld.append((ref, unreadable_pages, "unreadable_mark", None))
        elif not usable and inside:
            withheld.append((ref, inside, "within_citing_text", None))
        else:
            released.append(ref)
    return released, withheld


def _pages_phrase(pages: list[int], policy: dict) -> str:
    if len(pages) == 1:
        return policy["page_one"].format(page=pages[0])
    listed = policy["page_list_separator"].join(str(p) for p in pages[:-1])
    listed += policy["page_list_last"] + str(pages[-1])
    return policy["page_many"].format(pages=listed)


def _year(ref: ReferenceLike, policy: dict) -> str:
    if ref.year is not None:
        return str(ref.year)
    if reference_date_key(ref) == "explicit_no_date":
        return policy["year_no_date"]
    return policy["year_unavailable"]


def unread_page_leads(
    withheld: list[Withheld], *, numbered: bool, settings: Settings
) -> list[CitationFlagDraft]:
    """One lead per withheld entry. It names the pages to check and what was
    found on them, says the entry may be cited there, and does not change
    readiness."""
    policy = load_policy(settings.citation_unread_pages_policy_file)
    leads = []
    for ref, pages, why, found in withheld:
        number = _number(ref)
        if numbered and number is not None:
            entry = policy["entry_numbered"].format(number=number)
        else:
            entry = policy["entry_author_year"].format(
                first_author=(ref.authors or [policy["author_unknown"]])[0],
                year=_year(ref, policy),
            )
        phrase = _pages_phrase(pages, policy)
        if why == "named":
            template = policy["lead_named_number" if numbered else "lead_named_name"]
        elif why == "unreadable_mark":
            template = policy["lead_unreadable_mark"]
        else:
            template = policy["lead_within_citing_text"]
        reason = template.format(
            entry=entry,
            pages=phrase,
            Pages=phrase[:1].upper() + phrase[1:],
            found=found or "",
            lies=policy["lies_one" if len(pages) == 1 else "lies_many"],
        )
        detail = {
            "kind": "uncited_reference",
            "reason": reason,
            "order_index": ref.order_index,
            "affects_readiness": False,
            "withheld_by": "unread_pages",
            "withheld_because": why,
            "unread_pages": pages,
            "found_on_unread_pages": found,
            "policy_version": policy["version"],
        }
        if numbered:
            detail["number"] = number
        else:
            detail["authors"] = list(ref.authors or [])
            detail["year"] = ref.year
        leads.append(
            CitationFlagDraft(
                severity=FlagSeverity.low,
                evidence_excerpt=ref.raw_text,
                page_anchor=reference_anchor(ref.raw_text, ref.order_index),
                detail=detail,
            )
        )
    return leads
