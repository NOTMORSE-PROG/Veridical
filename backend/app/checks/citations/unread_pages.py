"""Which uncited-reference claims a page left unread could change (BUG-619).

A page whose reading order is unresolved is left out of the checks (BUG-507),
but its text is still there, out of order. Before this, one such page withheld
every uncited claim in the thesis, even when the page was an appendix table
that named none of the entries.

An entry is now called uncited only when no unread page names it: its first
author's surname for an author-year list, its bracket number for a numbered
one. An entry with no usable surname falls back to where the citing text is:
an unread page among the pages that cite sources withholds it, and so does a
thesis whose citing pages could not be found. Whatever stays withheld is shown
as a lead that names the pages to check and does not change readiness.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from app.checks.citations.extract import CitationFlagDraft, ReferenceLike
from app.checks.citations.numbered import reference_anchor
from app.config import Settings
from app.ingest.schemas import ExtractionResult
from app.models.enums import FlagSeverity

_DEFAULT_FILE = Path(__file__).parent / "data" / "unread_pages_linkage.json"
_WORD = re.compile(r"[^\W\d_]+")


@lru_cache(maxsize=4)
def load_policy(path: Path | None = None) -> dict:
    return json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))


def _surname_words(ref: ReferenceLike, min_chars: int) -> list[str]:
    """The first author's surname, as the words long enough to search for.
    "Black, S." gives ["black"]; an organisation gives its longer words."""
    authors = ref.authors or []
    if not authors:
        return []
    name = authors[0]
    surname = name.split(",", 1)[0] if "," in name else name
    return [w.lower() for w in _WORD.findall(surname) if len(w) >= min_chars]


def _number(ref: ReferenceLike) -> int | None:
    label = reference_anchor(ref.raw_text, ref.order_index).removeprefix("reference #")
    return int(label) if label.isdigit() else None


def split_by_unread_pages(
    candidates: list[ReferenceLike],
    extraction: ExtractionResult,
    *,
    numbered: bool,
    citing_pages: set[int],
    settings: Settings,
) -> tuple[list[ReferenceLike], list[tuple[ReferenceLike, list[int], str]]]:
    """The candidates no unread page could be citing, and the rest with the
    pages that could and why ("named" or "within_citing_text")."""
    unread = sorted(extraction.unread_pages or [])
    if not unread:
        return list(candidates), []
    texts = {page: (extraction.unread_text or {}).get(page, "").lower() for page in unread}
    policy = load_policy(settings.citation_unread_pages_policy_file)
    span = (min(citing_pages), max(citing_pages)) if citing_pages else None
    inside = [p for p in unread if span is None or span[0] <= p <= span[1]]
    released, withheld = [], []
    for ref in candidates:
        if numbered:
            number = _number(ref)
            pattern = (
                re.compile(policy["numbered_marker_pattern"].replace("{number}", str(number)))
                if number is not None
                else None
            )
            named = [p for p in unread if pattern and pattern.search(texts[p])]
            usable = pattern is not None
        else:
            words = _surname_words(ref, settings.citation_linkage_name_word_min_chars)
            named = [
                p for p in unread if any(re.search(rf"\b{re.escape(w)}\b", texts[p]) for w in words)
            ]
            usable = bool(words)
        if named:
            withheld.append((ref, named, "named"))
        elif not usable and inside:
            withheld.append((ref, inside, "within_citing_text"))
        else:
            released.append(ref)
    return released, withheld


def _pages_phrase(pages: list[int], policy: dict) -> str:
    if len(pages) == 1:
        return policy["page_one"].format(page=pages[0])
    listed = ", ".join(str(p) for p in pages[:-1]) + f" and {pages[-1]}"
    return policy["page_many"].format(pages=listed)


def unread_page_leads(
    withheld: list[tuple[ReferenceLike, list[int], str]], *, numbered: bool, settings: Settings
) -> list[CitationFlagDraft]:
    """One lead per withheld entry. It names the pages to check, says the
    entry may be cited there, and does not change readiness."""
    policy = load_policy(settings.citation_unread_pages_policy_file)
    leads = []
    for ref, pages, why in withheld:
        number = _number(ref)
        if numbered and number is not None:
            entry = policy["entry_numbered"].format(number=number)
        else:
            entry = policy["entry_author_year"].format(
                first_author=(ref.authors or ["Unknown"])[0],
                year=ref.year if ref.year is not None else "date unavailable",
            )
        phrase = _pages_phrase(pages, policy)
        template = policy["lead_named" if why == "named" else "lead_within_citing_text"]
        reason = template.format(
            entry=entry,
            pages=phrase,
            Pages=phrase[:1].upper() + phrase[1:],
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
