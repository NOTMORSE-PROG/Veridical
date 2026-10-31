"""F5 separates exact registry identity from cited metadata agreement."""

import html
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import quote

from pydantic import HttpUrl, TypeAdapter, ValidationError

from app.checks.citations.title_policy import TitlePolicy, compare_titles, load_title_policy
from app.config import Settings
from app.ingest.reference_metadata_patterns import load_reference_metadata_patterns

_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def same_doi(left, right):
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and left.translate(_ASCII_FOLD) == right.translate(_ASCII_FOLD)
    )


def _obj(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return value if isinstance(value, list) else []


def returned_doi(result):
    raw = _obj(result.raw)
    if result.provider == "crossref":
        return raw.get("DOI")
    if result.provider == "datacite":
        return _obj(_obj(raw.get("data")).get("attributes")).get("doi")
    if result.provider == "s2":
        return _obj(raw.get("externalIds")).get("DOI")
    return None


def _words(value):
    return re.findall(r"\w+", value.casefold()) if isinstance(value, str) else []


@dataclass(frozen=True)
class MetadataComparison:
    differing_fields: tuple[str, ...]
    title_status: str
    unassessed_reason: str | None
    cited: Mapping
    registered: Mapping
    policy_version: str
    policy_sha256: str
    provider: str
    doi_source_url: str | None = None
    provider_record_url: str | None = None
    author_status: str = "not_recorded"
    author_unassessed_reason: str | None = None

    def evidence(self):
        return {
            "schema_version": "doi-metadata-v1",
            "differing_fields": list(self.differing_fields),
            "title_status": self.title_status,
            "unassessed_reason": self.unassessed_reason,
            "cited": dict(self.cited),
            "registered": dict(self.registered),
            "policy_version": self.policy_version,
            "policy_sha256": self.policy_sha256,
            "provider": self.provider,
            "doi_source_url": self.doi_source_url,
            "provider_record_url": self.provider_record_url,
            "author_status": self.author_status,
            "author_unassessed_reason": self.author_unassessed_reason,
        }


def _record_url(template, doi):
    if not template or not isinstance(doi, str) or not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        return None
    try:
        return str(TypeAdapter(HttpUrl).validate_python(template.format(doi=quote(doi, safe=""))))
    except (ValueError, KeyError, IndexError, AttributeError, ValidationError):
        return None


# Inline markup registries put in titles: JATS/HTML tags such as <i>, <sub>,
# <scp>, <mml:mi>. A tag name starts with a letter, so "a <5 year" is text.
_REGISTRY_TAG = re.compile(r"</?[A-Za-z][\w.:-]*(?:\s[^<>]*)?/?>")


def registry_plain_title(title):
    """A registry title as a reader sees it: tags dropped, entities unescaped,
    spaces collapsed. "<i>Drynaria quercifolia</i>" is "Drynaria quercifolia"."""
    if not isinstance(title, str):
        return title
    return " ".join(html.unescape(_REGISTRY_TAG.sub("", title)).split())


@dataclass(frozen=True)
class _RegistryName:
    raw: str  # the first creator exactly as the registry returned it
    stripped: str  # the same with footnote markers dropped
    first: str  # the first name in it, when it packs several
    single: bool  # one clean name: a mismatch with it can be trusted


# Characters a clean personal or corporate name is made of. Anything else
# (separators, superscripts, symbols) means the registry string is not one
# plain name, and a disagreement with it proves nothing.
_NAME_PUNCTUATION = frozenset(" .'\u2019-\u2010\u2011")
_NAME_LETTERS = frozenset({"Lu", "Ll", "Lt", "Lo"})


def _clean_single_name(name):
    return bool(name.strip()) and all(
        ch in _NAME_PUNCTUATION or unicodedata.category(ch) in _NAME_LETTERS for ch in name
    )


def _registry_first_author(name, settings):
    """Read the registry's first creator string.

    Some records hold every author in one creator string with footnote
    markers ("Mbonigaba Celestin*, S. Sujatha** & M. Vasuki****"). Markers
    are dropped and a packed string is cut at its separators, keeping its
    first name without claiming which word of it is the surname."""
    patterns = load_reference_metadata_patterns(
        settings.reference_metadata_patterns_file if settings else None
    )
    stripped = " ".join(patterns.registry_author_marker.sub("", name).split())
    pieces = [p.strip() for p in patterns.registry_author_list.split(stripped) if p.strip()]
    return _RegistryName(
        raw=name,
        stripped=stripped,
        first=pieces[0] if pieces else stripped,
        # "Cruz et al." splits into one piece, but it is not one plain name.
        single=pieces == [stripped] and _clean_single_name(name),
    )


def compare_metadata(
    citation, result, *, policy: TitlePolicy | None = None, settings: Settings | None = None
):
    policy = policy or load_title_policy()
    differences = []
    title_status, title_reason = compare_titles(
        citation.title, registry_plain_title(result.title), policy
    )
    if title_status == "differs":
        differences.append("title")
    year, authors = None, []
    raw = _obj(result.raw)
    if result.provider == "crossref":
        for field in ("published", "published-print", "published-online", "issued"):
            parts = _rows(_obj(raw.get(field)).get("date-parts"))
            if parts and _rows(parts[0]):
                year = parts[0][0]
                break
        authors = [_obj(item).get("family") for item in _rows(raw.get("author"))]
    elif result.provider == "datacite":
        attributes = _obj(_obj(raw.get("data")).get("attributes"))
        year = attributes.get("publicationYear")
        authors = [_obj(item).get("familyName") for item in _rows(attributes.get("creators"))]
    if citation.year is not None and type(year) is int and citation.year != year:
        differences.append("year")
    author_status, author_reason = "not_recorded", None
    registered_first = None
    if citation.authors and authors and isinstance(authors[0], str) and authors[0]:
        name = _registry_first_author(authors[0], settings)
        cited = citation.authors[0].split(",", 1)[0]
        registered_first = name.raw
        if _words(cited) == _words(name.raw):
            author_status = "agrees"
        elif _words(cited) == _words(name.stripped):
            author_status, registered_first = "agrees", name.stripped
        else:
            from app.checks.citations.extract import (
                _is_corporate_author_name,
                source_supported_corporate_names,
            )

            if (
                len(citation.authors) == 1
                and _is_corporate_author_name(cited)
                and re.search(r"\s+(?:and|&)\s+", cited, re.IGNORECASE)
            ):
                supported = source_supported_corporate_names(cited, authors)
                if supported is not None:
                    author_status = "source_supported_sequence"
                else:
                    author_status, author_reason = "unassessed", "author_boundaries_unassessed"
            elif name.single:
                differences.append("first author")
                author_status = "differs"
            else:
                # BUG-601: a packed or marked-up creator string does not say
                # which word is the surname, so it can confirm the cited
                # name but never establish a mismatch.
                registered_first = name.first
                if _words(cited) and set(_words(cited)) <= set(_words(name.first)):
                    author_status = "agrees"
                else:
                    author_status = "unassessed"
                    author_reason = "registry_author_name_not_separated"
    elif authors and isinstance(authors[0], str) and authors[0]:
        registered_first = authors[0]
    return MetadataComparison(
        tuple(differences),
        title_status,
        title_reason,
        MappingProxyType(
            {
                "doi": citation.doi,
                "title": citation.title,
                "year": citation.year,
                "first_author": citation.authors[0] if citation.authors else None,
            }
        ),
        MappingProxyType(
            {
                "doi": returned_doi(result) if isinstance(returned_doi(result), str) else None,
                "title": result.title if isinstance(result.title, str) else None,
                "year": year if type(year) is int else None,
                "first_author": registered_first,
            }
        ),
        policy.version,
        policy.sha256,
        result.provider,
        _record_url(settings.citation_doi_source_url_template, citation.doi) if settings else None,
        _record_url(
            settings.citation_registry_record_url_templates.get(result.provider),
            returned_doi(result),
        )
        if settings
        else None,
        author_status=author_status,
        author_unassessed_reason=author_reason,
    )


def metadata_mismatches(citation, result):
    return list(compare_metadata(citation, result).differing_fields)
