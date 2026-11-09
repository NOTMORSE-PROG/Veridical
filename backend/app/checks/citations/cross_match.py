"""Citation cross-matching against reference list (F5.1).

Matches in-text author-year and numbered citations against the reference list,
supporting corporate author acronyms/initialisms (e.g. WHO <-> World Health Organization,
IEEE, ACM, CHED, DOST, etc.) and numbered bracket citations ([1], [1]-[3]).
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from app.checks.citations.numbered import explicit_reference_label
from app.config import get_settings
from app.ingest.citation_dates import citation_date_key, reference_date_key

if TYPE_CHECKING:
    from app.checks.citations.extract import CrossMatchResult, InTextCitation


class ReferenceLike(Protocol):
    order_index: int
    raw_text: str
    authors: list[str] | None
    year: int | str | None


logger = logging.getLogger(__name__)

_NAME_VARIANTS_FILE = Path(__file__).parent / "data" / "name_variants.json"
_INITIALS_TOKEN = re.compile(r"(?:[^\W\d_]\.)+(?:-[^\W\d_]\.)*")

# Bidirectional known alias dictionary for prominent global & Philippine technical/academic bodies
_KNOWN_ACRONYMS: dict[str, list[str]] = {
    "who": ["world health organization"],
    "world health organization": ["who"],
    "ieee": ["institute of electrical and electronics engineers"],
    "institute of electrical and electronics engineers": ["ieee"],
    "acm": ["association for computing machinery"],
    "association for computing machinery": ["acm"],
    "ched": ["commission on higher education"],
    "commission on higher education": ["ched"],
    "dost": ["department of science and technology"],
    "department of science and technology": ["dost"],
    "unesco": ["united nations educational, scientific and cultural organization"],
    "united nations educational, scientific and cultural organization": ["unesco"],
    "fao": ["food and agriculture organization"],
    "food and agriculture organization": ["fao"],
    "iso": ["international organization for standardization"],
    "international organization for standardization": ["iso"],
    "nasa": ["national aeronautics and space administration"],
    "national aeronautics and space administration": ["nasa"],
    "unicef": ["united nations children's fund"],
    "united nations children's fund": ["unicef"],
    "cdc": ["centers for disease control and prevention"],
    "centers for disease control and prevention": ["cdc"],
    "fda": ["food and drug administration"],
    "food and drug administration": ["fda"],
    "nih": ["national institutes of health"],
    "national institutes of health": ["nih"],
    "psa": ["philippine statistics authority"],
    "philippine statistics authority": ["psa"],
    "deped": ["department of education"],
    "department of education": ["deped"],
    "neda": ["national economic and development authority"],
    "national economic and development authority": ["neda"],
    "doh": ["department of health"],
    "department of health": ["doh"],
    "dict": ["department of information and communications technology"],
    "department of information and communications technology": ["dict"],
    "dpwh": ["department of public works and highways"],
    "department of public works and highways": ["dpwh"],
    "dti": ["department of trade and industry"],
    "department of trade and industry": ["dti"],
    "un": ["united nations"],
    "united nations": ["un"],
}

_STOP_WORDS = frozenset({"of", "and", "the", "on", "for", "in", "to", "a", "an", "&"})
_NUMBERED_RANGE = re.compile(r"([1-9]\d*)\s*[-\u2013—]\s*([1-9]\d*)")
_BRACKETED_NUMBER = re.compile(r"\[\s*([1-9]\d*(?:\s*[-\u2013—,;]\s*[1-9]\d*)*)\s*\]")
_MULTI_BRACKET_RANGE = re.compile(r"\[\s*([1-9]\d*)\s*\]\s*[-\u2013—]\s*\[\s*([1-9]\d*)\s*\]")
_BRACKETED_CONTENT = re.compile(r"\[\s*([^\]]+)\s*\]")


def get_author_aliases(name: str) -> set[str]:
    """Return normalized aliases and initialisms for an author or organization name,
    including dotted initialisms (e.g. W.H.O. <-> WHO <-> World Health Organization).
    """
    cleaned = " ".join(name.split()).casefold()
    if not cleaned:
        return set()
    aliases = {cleaned}
    if cleaned in _KNOWN_ACRONYMS:
        for alias in _KNOWN_ACRONYMS[cleaned]:
            aliases.add(alias.casefold())

    # Dotted or spaced acronym normalization (e.g. "w.h.o." or "w. h. o." -> "who")
    unpunctuated = cleaned.replace(".", "").replace(" ", "").strip()
    if unpunctuated and unpunctuated != cleaned:
        aliases.add(unpunctuated)
        if unpunctuated in _KNOWN_ACRONYMS:
            for alias in _KNOWN_ACRONYMS[unpunctuated]:
                aliases.add(alias.casefold())

    # Generate initialism from significant words
    words = [w for w in name.split() if w.lower() not in _STOP_WORDS]
    if len(words) >= 2:
        initialism = "".join(w[0] for w in words if w and w[0].isalnum()).casefold()
        if len(initialism) >= 2:
            aliases.add(initialism)
            # Check if this initialism has known full names
            if initialism in _KNOWN_ACRONYMS:
                for full_name in _KNOWN_ACRONYMS[initialism]:
                    aliases.add(full_name.casefold())

    return aliases


def _first_reference_surname(ref: ReferenceLike) -> str | None:
    for author in ref.authors or []:
        surname = author.split(",", 1)[0].strip() if "," in author else author.strip()
        if surname:
            return " ".join(surname.split()).casefold()
    return None


def _reference_surnames(ref: ReferenceLike) -> set[str]:
    out: set[str] = set()
    for author in ref.authors or []:
        surname = author.split(",", 1)[0].strip() if "," in author else author.strip()
        if surname:
            out.add(" ".join(surname.split()).casefold())
    return out


def parse_numeric_citation_numbers(cite: InTextCitation) -> list[int] | None:
    """Extract list of referenced integers from a numbered citation mark (e.g. [1], [1]-[3])."""
    # If the citation has alphabetic author surnames, it's an author-year citation, not numbered
    if cite.surnames and any(any(c.isalpha() for c in s) for s in cite.surnames):
        return None

    raw = (cite.raw_text or "").strip()

    # Check multi-bracket range like [1]-[3] or [1]–[3]
    range_m = _MULTI_BRACKET_RANGE.search(raw)
    if not range_m and cite.surnames:
        range_m = _MULTI_BRACKET_RANGE.search(cite.surnames[0].strip())
    if range_m:
        start, end = int(range_m.group(1)), int(range_m.group(2))
        if start <= end and (end - start) <= 50:
            return list(range(start, end + 1))

    bracketed = _BRACKETED_CONTENT.findall(raw)
    contents: list[str] = []
    if bracketed:
        contents.extend(bracketed)
    elif cite.surnames:
        first = cite.surnames[0].strip()
        first_bracketed = _BRACKETED_CONTENT.findall(first)
        if first_bracketed:
            contents.extend(first_bracketed)
        elif re.fullmatch(r"[1-9]\d*(?:\s*[-\u2013—,;]\s*[1-9]\d*)*", first):
            contents.append(first)

    if not contents:
        return None

    numbers: list[int] = []
    for c in contents:
        # Split by comma or semicolon
        for part in re.split(r"[,;]", c):
            part = part.strip()
            if not part:
                continue
            range_sub = _NUMBERED_RANGE.fullmatch(part)
            if range_sub:
                start, end = int(range_sub.group(1)), int(range_sub.group(2))
                if start <= end and (end - start) <= 50:
                    numbers.extend(range(start, end + 1))
            elif part.isdigit():
                numbers.append(int(part))
            else:
                return None

    return list(dict.fromkeys(numbers)) if numbers else None


def _build_reference_number_index(references: list[ReferenceLike]) -> dict[int, ReferenceLike]:
    """Map reference numbers to references by explicit label ([1], 1.) or ordinal order_index."""
    by_number: dict[int, ReferenceLike] = {}
    for ref in references:
        raw = ref.raw_text or ""
        label = explicit_reference_label(raw)
        if label:
            num_str = re.search(r"\d+", label)
            if num_str:
                by_number[int(num_str.group(0))] = ref
        # Also index by 1-based order_index as fallback
        ordinal = ref.order_index + 1
        if ordinal not in by_number:
            by_number[ordinal] = ref
    return by_number


@lru_cache(maxsize=4)
def load_name_variant_policy(path: Path | None = None) -> dict:
    return json.loads((path or _NAME_VARIANTS_FILE).read_text(encoding="utf-8"))


def _known_expansions(name: str) -> set[str]:
    key = " ".join(name.split()).casefold()
    keys = {key, key.replace(".", "").replace(" ", "")}
    return {alias.casefold() for k in keys for alias in _KNOWN_ACRONYMS.get(k, ())}


def _link_name_variants(orphans, references, linked, cited, possibly_cited):
    """Link a citation left without an entry to the one entry it names in another
    spelling (BUG-696): the same surname with hyphens or spaces dropped, or an
    organisation's short and long forms. A citation is linked only when exactly
    one same-year entry fits it by name. An organisation form also needs an
    entry nothing else cites and the entry's title in the citing sentence,
    counting only words that are not part of either name. Anything else stays
    an orphan."""
    from app.checks.citations.extract import _is_corporate_author_name

    if not linked:
        # When the main matching linked nothing, the citations were not read,
        # not respelt; the whole list is withheld for that elsewhere.
        return orphans
    try:
        policy = load_name_variant_policy(get_settings().citation_name_variant_policy_file)
        enabled = bool(policy["enabled"])
        fold = re.compile(policy["fold_pattern"])
        word = re.compile(policy["title_word_pattern"])
        min_chars = int(policy["organisation_prefix_min_chars"])
        min_words = int(policy["title_evidence_min_words"])
        min_share = float(policy["title_evidence_min_share"])
        ignored = {w.casefold() for w in policy.get("title_ignored_words", ())}
    except (OSError, ValueError, KeyError, TypeError, re.error) as exc:
        logger.warning("Citation name-variant policy unusable, variant linking skipped: %s", exc)
        return orphans
    if not enabled:
        return orphans
    # Entries cited before this pass; every citation is judged against the same set.
    taken = set(cited) | set(possibly_cited)

    def folded(name):
        return fold.sub("", (name or "").casefold())

    def words(text):
        return {w for w in word.findall((text or "").casefold()) if w not in ignored}

    def name_fit(cited_name, entry_name):
        if folded(cited_name) and folded(cited_name) == folded(entry_name):
            return "surname" if cited_name.casefold() != entry_name.casefold() else None
        short, long_ = sorted(
            (cited_name.casefold().split(), entry_name.casefold().split()), key=len
        )
        if (
            len(long_) <= len(short)
            or long_[: len(short)] != short
            or len("".join(short)) < min_chars
        ):
            return None
        if not _is_corporate_author_name(" ".join(long_)):
            # Only an organisation has a short and a long form: "Garcia" and
            # "Garcia Lopez M." are two different people.
            return None
        known = _known_expansions(cited_name)
        if known and " ".join(entry_name.casefold().split()) not in known:
            # The acronym table says this short form names another body.
            return None
        return "organisation"

    def title_in_sentence(cite, ref, entry_name):
        names = words(cite.surnames[0]) | words(entry_name)
        title = words(getattr(ref, "title", None)) - names
        shared = title & (words(cite.claim_sentence) - names)
        return bool(title) and len(shared) >= min_words and len(shared) / len(title) >= min_share

    remaining = []
    for cite in orphans:
        fitting = []
        if cite.surnames and cite.year:
            for ref in references:
                if reference_date_key(ref) != citation_date_key(cite.year):
                    continue
                author = next((a for a in ref.authors or [] if a and a.strip()), "")
                entry_name = author.split(",", 1)[0].strip()
                kind = name_fit(cite.surnames[0], entry_name)
                if kind:
                    fitting.append((ref, kind, entry_name))
        if len(fitting) == 1:
            ref, kind, entry_name = fitting[0]
            if kind == "surname" or (
                ref.order_index not in taken and title_in_sentence(cite, ref, entry_name)
            ):
                linked.append((cite, [ref]))
                cited.add(ref.order_index)
                continue
        remaining.append(cite)
    return remaining


def cross_match(in_text: list[InTextCitation], references: list[ReferenceLike]) -> CrossMatchResult:
    from app.checks.citations.extract import (
        CrossMatchResult,
        _is_corporate_author_name,
        source_supported_corporate_names,
    )

    index: dict[str, list[ReferenceLike]] = {}
    possible_names: dict[str, list[ReferenceLike]] = {}
    name_limit = get_settings().citation_author_max_tokens

    for ref in references:
        for surname in _reference_surnames(ref):
            for alias in get_author_aliases(surname):
                index.setdefault(alias, []).append(ref)
        for author in ref.authors or []:
            if "," in author:
                continue
            words = author.split()
            for size in range(1, min(len(words), name_limit) + 1):
                possible_names.setdefault(" ".join(words[-size:]).casefold(), []).append(ref)
            leading = list(words)
            while (
                len(leading) > 1
                and leading[-1].isupper()
                and _INITIALS_TOKEN.fullmatch(leading[-1])
            ):
                leading.pop()
            if len(leading) < len(words):
                for size in range(1, min(len(leading), name_limit) + 1):
                    possible_names.setdefault(" ".join(leading[-size:]).casefold(), []).append(ref)

    ref_by_number = _build_reference_number_index(references)

    linked: list[tuple[InTextCitation, list[ReferenceLike]]] = []
    orphans: list[InTextCitation] = []
    ambiguous: list[InTextCitation] = []
    cited_order_indexes: set[int] = set()
    possibly_cited: set[int] = set()

    for cite in in_text:
        if cite.is_secondary:
            continue

        # Check for numbered citation first ([1], [1]-[3])
        numeric_nums = parse_numeric_citation_numbers(cite)
        if numeric_nums is not None:
            matched_refs = [ref_by_number[n] for n in numeric_nums if n in ref_by_number]
            if matched_refs:
                linked.append((cite, matched_refs))
                cited_order_indexes.update(r.order_index for r in matched_refs)
            else:
                orphans.append(cite)
            continue

        surnames = list(cite.surnames)
        if (
            len(surnames) == 1
            and _is_corporate_author_name(surnames[0])
            and re.search(r"\s+(?:and|&)\s+", surnames[0], re.IGNORECASE)
        ):
            same_year = [
                ref for ref in references if reference_date_key(ref) == citation_date_key(cite.year)
            ]
            complete = [
                ref
                for ref in same_year
                if source_supported_corporate_names(surnames[0], ref.authors or []) is not None
            ]
            if len(complete) == 1:
                linked.append((cite, complete))
                cited_order_indexes.add(complete[0].order_index)
            else:
                ambiguous.append(cite)
                # Unresolved corporate boundaries cannot establish that a
                # same-year source was unused, nor justify an orphan accusation.
                possibly_cited.update(ref.order_index for ref in same_year)
            continue
        candidates: list[ReferenceLike] = []
        seen_ids: set[int] = set()

        for surname in surnames:
            for alias in get_author_aliases(surname):
                for ref in index.get(alias, []):
                    if ref.order_index not in seen_ids:
                        seen_ids.add(ref.order_index)
                        candidates.append(ref)

        if not candidates and len(surnames) == 1 and len(surnames[0].split()) > 1:
            words = surnames[0].split()
            for size in range(len(words) - 1, 0, -1):
                suffix = " ".join(words[-size:]).casefold()
                for alias in get_author_aliases(suffix):
                    if alias in index:
                        surnames = [suffix]
                        candidates = list(index[alias])
                        break
                if candidates:
                    break

        year_digits = citation_date_key(cite.year)
        matches = [c for c in candidates if reference_date_key(c) == year_digits]
        matched_ids = {ref.order_index for ref in matches}

        possible_keys = {
            " ".join(words[-size:]).casefold()
            for surname in cite.surnames
            if (words := surname.split())
            for size in range(1, min(len(words), name_limit) + 1)
        }
        uncertain_names = {
            ref.order_index: ref
            for key in possible_keys
            for ref in possible_names.get(key, [])
            if reference_date_key(ref) == year_digits and ref.order_index not in matched_ids
        }
        if uncertain_names:
            ambiguous.append(cite)
            possibly_cited.update(matched_ids | uncertain_names.keys())
            continue

        if len(matches) > 1 and cite.year and cite.year[-1].isalpha():
            refined = [c for c in matches if cite.year in (c.raw_text or "")]
            if refined:
                matches = refined

        cite_surnames = {" ".join(s.split()).casefold() for s in surnames}
        cite_aliases = {alias for s in cite_surnames for alias in get_author_aliases(s)}
        lead_matches = [
            c
            for c in matches
            if any(
                alias in cite_aliases
                for alias in get_author_aliases(_first_reference_surname(c) or "")
            )
        ]
        coauthor_only = bool(matches) and not lead_matches
        if lead_matches:
            matches = lead_matches

        if coauthor_only or len(matches) > 1:
            ambiguous.append(cite)
            possibly_cited.update(c.order_index for c in matches)
        elif matches:
            cited_order_indexes.update(c.order_index for c in matches)
            linked.append((cite, matches))
        else:
            orphans.append(cite)

    orphans = _link_name_variants(orphans, references, linked, cited_order_indexes, possibly_cited)
    uncited = [r for r in references if r.order_index not in cited_order_indexes]
    return CrossMatchResult(
        in_text=in_text,
        linked=linked,
        orphans=orphans,
        uncited=uncited,
        ambiguous=ambiguous,
        possibly_cited=[r for r in references if r.order_index in possibly_cited],
        confidently_uncited=[r for r in uncited if r.order_index not in possibly_cited],
    )
