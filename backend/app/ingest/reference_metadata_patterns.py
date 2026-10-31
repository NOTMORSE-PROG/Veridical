"""Data-backed conventions for a bounded numbered-reference fallback (F1/F5)."""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class ReferenceMetadataPatterns:
    access_note: re.Pattern[str]
    publication_year: re.Pattern[str]
    name_particles: frozenset[str]
    corporate_author_words: frozenset[str]
    # Words that keep a long, lowercase-bearing name an author when a title
    # might stand before the date ("Republic of the Philippines").
    author_institution_words: frozenset[str]
    locator_label: re.Pattern[str] | None
    doi_resolver_hosts: frozenset[str]
    date_source: str
    standard_identifier: re.Pattern[str]
    quoted_title_pairs: tuple[tuple[str, str], ...]
    quoted_title_separators: str
    quoted_title_terminal_delimiters: str
    # Markup some document exports leave in printed reference text:
    # "[shown](target)" links and "*shown*" emphasis. A link target that wraps
    # another, URL-encoded address is a redirect, never the work's locator.
    markup_link: re.Pattern[str]
    markup_emphasis: re.Pattern[str]
    markup_redirect: re.Pattern[str]
    # Shown link text that is already an address or identifier.
    markup_shown_locator: re.Pattern[str]
    # Registries sometimes return all authors as one creator string with
    # footnote markers ("Mbonigaba Celestin*, S. Sujatha** & ..."): the
    # markers to drop and the separators between packed names.
    registry_author_marker: re.Pattern[str]
    registry_author_list: re.Pattern[str]
    version: str
    sha256: str


@lru_cache
def load_reference_metadata_patterns(path: Path | None = None) -> ReferenceMetadataPatterns:
    source = path or Path(__file__).parent / "data" / "reference_metadata_patterns.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    defaults = json.loads(
        (Path(__file__).parent / "data" / "reference_metadata_patterns.json").read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(raw, dict):
        raise ValueError("Reference metadata configuration must be an object")
    effective = defaults | raw
    raw = effective
    pairs = effective["quoted_title_pairs"]
    if (
        not isinstance(pairs, list)
        or not pairs
        or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(char, str) or len(char) != 1 for char in pair)
            for pair in pairs
        )
        or len({pair[0] for pair in pairs}) != len(pairs)
    ):
        raise ValueError("Quoted title pairs must have unique single-character openers")
    for opening, closing in pairs:
        if (
            any("QUOTATION MARK" not in unicodedata.name(char, "") for char in (opening, closing))
            or unicodedata.category(opening) not in {"Pi", "Po"}
            or unicodedata.category(closing) not in {"Pf", "Po"}
            or (unicodedata.category(opening) == "Po" and opening != closing)
        ):
            raise ValueError("Quoted title pairs must contain correctly oriented quotation marks")
    for name in ("quoted_title_separators", "quoted_title_terminal_delimiters", "version"):
        if not isinstance(effective[name], str) or not effective[name]:
            raise ValueError(f"Reference metadata {name} must be nonempty text")
    quote_chars = {char for pair in pairs for char in pair}
    for name in ("quoted_title_separators", "quoted_title_terminal_delimiters"):
        if any(
            not unicodedata.category(char).startswith("P") or char in quote_chars
            for char in effective[name]
        ):
            raise ValueError(f"Reference metadata {name} must contain non-quote punctuation")
    if not set(effective["quoted_title_terminal_delimiters"]) <= set(
        effective["quoted_title_separators"]
    ):
        raise ValueError("Quoted terminal delimiters must also be title separators")
    no_dates = raw.get("no_date_tokens", defaults["no_date_tokens"])
    if not isinstance(no_dates, list) or not all(
        isinstance(token, str) and token.strip() for token in no_dates
    ):
        raise ValueError("No-date tokens must be a list of nonempty strings")
    alternatives = "|".join(
        rf"{re.sub(r'(\\ )+', r'\\s+', re.escape(token))}[a-z]?" for token in no_dates
    )
    date_source = r"\d{4}[a-z]?" + (rf"|(?i:{alternatives})" if alternatives else "")
    for name in (
        "access_note_prefixes",
        "months",
        "name_particles",
        "corporate_author_words",
    ):
        values = raw[name]
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(value, str) and value.strip() for value in values)
        ):
            raise ValueError(f"Reference metadata {name} must be a nonempty list of words")
    access = "|".join(re.escape(value) for value in raw["access_note_prefixes"])
    months = "|".join(re.escape(value) for value in raw["months"])
    # Older instructor-owned pattern files need not opt into this addition.
    # Missing/empty labels keep their previous behavior instead of breaking all
    # otherwise-supported numbered references during metadata extraction.
    locator_labels = raw.get("locator_labels", [])
    if not isinstance(locator_labels, list) or not all(
        isinstance(value, str) and value.strip() for value in locator_labels
    ):
        raise ValueError("Reference metadata locator_labels must be a list of words")
    labels = "|".join(re.escape(value) for value in locator_labels)
    hosts = raw.get(
        "doi_resolver_hosts",
        json.loads(
            (Path(__file__).parent / "data" / "reference_metadata_patterns.json").read_text(
                encoding="utf-8"
            )
        )["doi_resolver_hosts"],
    )
    if not isinstance(hosts, list) or not all(
        isinstance(host, str) and re.fullmatch(r"[a-zA-Z0-9.-]+", host) for host in hosts
    ):
        raise ValueError("DOI resolver hosts must be a list of plain host names")
    marker = _nonempty_pattern(effective["registry_author_marker_pattern"])
    author_list = _nonempty_pattern(effective["registry_author_list_pattern"])
    # Known answers: a plain name passes through untouched, a footnote star
    # is dropped, and two comma-separated names come apart. A pattern that
    # fails these would turn every registry author comparison into noise.
    if (
        marker.sub("", "Maria Dela Cruz") != "Maria Dela Cruz"
        or marker.sub("", "Santos*") != "Santos"
        or [p for p in author_list.split("Maria Dela Cruz") if p] != ["Maria Dela Cruz"]
        or len([p for p in author_list.split("Maria Santos, Jose Cruz") if p]) != 2
    ):
        raise ValueError("Registry author patterns fail their known-answer check")
    return ReferenceMetadataPatterns(
        access_note=_compile(
            rf"\b(?:{access})\s*(?::|on\b|from\b|(?=(?:{months})\.?\s+\d|\d))",
            re.IGNORECASE,
        ),
        publication_year=_compile(
            rf"(?:,\s*|\b(?:{months})\.?\s+(?:\d{{1,2}},?\s+)?)"
            r"(?P<year>1[89]\d{2}|20\d{2})(?=[.,;\s]|$)",
            re.IGNORECASE,
        ),
        name_particles=frozenset(word.casefold() for word in raw["name_particles"]),
        corporate_author_words=frozenset(word.casefold() for word in raw["corporate_author_words"]),
        author_institution_words=frozenset(
            word.casefold() for word in _word_list(effective, "author_institution_words")
        ),
        locator_label=(
            _compile(rf"\b(?:{labels})\s*:?\s*(?=https?://)", re.IGNORECASE) if labels else None
        ),
        doi_resolver_hosts=frozenset(host.lower() for host in hosts),
        date_source=f"(?:{date_source})",
        standard_identifier=_compile(
            raw.get("standard_identifier_pattern", defaults["standard_identifier_pattern"])
        ),
        quoted_title_pairs=tuple(tuple(pair) for pair in pairs),
        quoted_title_separators=effective["quoted_title_separators"],
        quoted_title_terminal_delimiters=effective["quoted_title_terminal_delimiters"],
        markup_link=_markup_pattern(effective["markup_link_pattern"], ("shown", "target")),
        markup_emphasis=_markup_pattern(effective["markup_emphasis_pattern"], ("shown",)),
        markup_redirect=_compile(effective["markup_redirect_pattern"]),
        markup_shown_locator=_compile(effective["markup_shown_locator_pattern"]),
        registry_author_marker=marker,
        registry_author_list=author_list,
        version=effective["version"],
        sha256=hashlib.sha256(
            json.dumps(effective, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
    )


def _word_list(config, name):
    values = config[name]
    if not isinstance(values, list) or not all(
        isinstance(value, str) and value.strip() for value in values
    ):
        raise ValueError(f"Reference metadata {name} must be a list of words")
    return values


def _markup_pattern(pattern, groups):
    compiled = _compile(pattern)
    if not set(groups) <= set(compiled.groupindex):
        raise ValueError(f"Reference markup pattern must name the groups {', '.join(groups)}")
    return compiled


def _nonempty_pattern(pattern):
    # A pattern that can match the empty string would split or strip a
    # name at every position.
    compiled = _compile(pattern)
    if compiled.fullmatch(""):
        raise ValueError("Reference metadata pattern must not match empty text")
    return compiled


def _compile(pattern, flags=0):
    try:
        return re.compile(pattern, flags)
    except (re.error, TypeError) as exc:
        raise ValueError("Reference metadata pattern is invalid") from exc
