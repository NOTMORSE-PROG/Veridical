"""What kind of work a looked-up reference is, as its provider recorded it.

Each provider names types its own way (Crossref "journal-article", Semantic
Scholar "JournalArticle", DataCite "Text"), so a type is kept with its
provider as "provider:type". Which of those count as a major reference or a
peer-reviewed source is data (checks/data/reference_criteria.json), not code.
"""

from typing import Any

from app.external.schemas import VerificationResult

# Book catalogues only ever return books.
_BOOK_PROVIDERS = {"openlibrary", "gbooks"}


def work_types(result: VerificationResult | None) -> list[str]:
    """The provider's own type tags for a found work, or [] when it gave none."""
    if result is None or not result.found:
        return []
    provider, raw = result.provider, result.raw or {}
    if provider in _BOOK_PROVIDERS:
        return [f"{provider}:book"]
    if provider == "crossref":
        return _tags(provider, [raw.get("type")])
    if provider == "s2":
        return _tags(provider, raw.get("publicationTypes") or [])
    if provider == "datacite":
        record = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        types = ((record.get("attributes") or {}).get("types")) or {}
        return _tags(provider, [types.get("resourceTypeGeneral")])
    return []


def _tags(provider: str, values: list[Any]) -> list[str]:
    return sorted({f"{provider}:{value}" for value in values if isinstance(value, str) and value})
