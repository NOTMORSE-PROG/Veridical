"""F5 separates exact registry identity from cited metadata agreement."""

import re

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


def metadata_mismatches(citation, result):
    differences = []
    if citation.title and result.title and _words(citation.title) != _words(result.title):
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
    if citation.authors and authors and isinstance(authors[0], str) and authors[0]:
        cited = citation.authors[0].split(",", 1)[0]
        if _words(cited) != _words(authors[0]):
            differences.append("first author")
    return differences
