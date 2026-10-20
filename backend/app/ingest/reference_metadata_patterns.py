"""Data-backed conventions for a bounded numbered-reference fallback (F1/F5)."""

import json
import re
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


@lru_cache
def load_reference_metadata_patterns(path: Path | None = None) -> ReferenceMetadataPatterns:
    source = path or Path(__file__).parent / "data" / "reference_metadata_patterns.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
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
        access_note=re.compile(
            rf"\b(?:{access})\s*(?::|on\b|from\b|(?=(?:{months})\.?\s+\d|\d))",
            re.IGNORECASE,
        ),
        publication_year=re.compile(
            rf"(?:,\s*|\b(?:{months})\.?\s+(?:\d{{1,2}},?\s+)?)"
            r"(?P<year>1[89]\d{2}|20\d{2})(?=[.,;\s]|$)",
            re.IGNORECASE,
        ),
        name_particles=frozenset(word.casefold() for word in raw["name_particles"]),
        corporate_author_words=frozenset(word.casefold() for word in raw["corporate_author_words"]),
        locator_label=(
            re.compile(rf"\b(?:{labels})\s*:?\s*(?=https?://)", re.IGNORECASE) if labels else None
        ),
        doi_resolver_hosts=frozenset(host.lower() for host in hosts),
    )
