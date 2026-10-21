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
    locator_label: re.Pattern[str] | None
    doi_resolver_hosts: frozenset[str]
    date_source: str
    standard_identifier: re.Pattern[str]
    quoted_title_pairs: tuple[tuple[str, str], ...]
    quoted_title_separators: str
    quoted_title_terminal_delimiters: str
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
    alternatives = "|".join(re.escape(token) for token in no_dates)
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
        version=effective["version"],
        sha256=hashlib.sha256(
            json.dumps(effective, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
    )


def _compile(pattern, flags=0):
    try:
        return re.compile(pattern, flags)
    except (re.error, TypeError) as exc:
        raise ValueError("Reference metadata pattern is invalid") from exc
