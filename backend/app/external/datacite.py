"""Public exact-DOI DataCite metadata (F5.6).

Resolves datasets, software, and non-Crossref DOIs (Zenodo, Dryad, Figshare)
using the DataCite REST API (https://api.datacite.org/dois/{doi}).
"""

import re
from functools import lru_cache
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings, get_settings
from app.errors import ApiDownError
from app.external.http import get_json
from app.external.schemas import ExternalWorkRecord, VerificationResult
from app.rate_governor import RateGovernor

__all__ = ["ExternalWorkRecord", "lookup_doi"]


@lru_cache
def _governor(rpm: int) -> RateGovernor:
    return RateGovernor(rpm=max(1, rpm))


async def lookup_doi(
    client: httpx.AsyncClient,
    doi: str,
    settings: Settings | None = None,
    **kwargs: Any,
) -> ExternalWorkRecord | None:
    """Query DataCite REST API for exact DOI resolution."""
    if settings is None:
        settings = kwargs.get("settings") or get_settings()

    clean_doi = re.sub(
        r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", "", doi.strip(), flags=re.IGNORECASE
    )
    base_url = getattr(settings, "datacite_base_url", "https://api.datacite.org").rstrip("/")
    url = f"{base_url}/dois/{quote(clean_doi, safe='')}"
    rpm = getattr(settings, "datacite_rpm", 60)
    contact = getattr(settings, "external_contact_email", "veridical@example.org")

    try:
        data = await get_json(
            client,
            url,
            provider="datacite",
            governor=_governor(rpm),
            max_retries=settings.external_max_retries,
            retry_base_seconds=settings.external_retry_base_seconds,
            headers={"User-Agent": f"VERIDICAL/1.0 (mailto:{contact})"},
        )
        if data is None:
            return VerificationResult(
                found=False, provider="datacite", attempted_providers=["datacite"]
            )
        if not isinstance(data, dict):
            raise ApiDownError("DataCite returned non-dict JSON structure.")
        record = data.get("data")
        if not isinstance(record, dict):
            raise ApiDownError("DataCite returned invalid or corrupt payload structure.")
        attributes = record.get("attributes")
        if not isinstance(attributes, dict):
            raise ApiDownError("DataCite record attributes are malformed or missing.")
        identifier = attributes.get("doi")
        titles = attributes.get("titles")
        title = None
        if isinstance(titles, list) and titles and isinstance(titles[0], dict):
            title = titles[0].get("title")

        if not identifier:
            identifier = record.get("id")

        if (
            record.get("type") != "dois"
            or not isinstance(identifier, str)
            or not title
        ):
            # Incomplete or non-DOI record
            raise ApiDownError("DataCite record does not match required DOI schema.")

        return VerificationResult(
            found=True,
            provider="datacite",
            title=title,
            url=attributes.get("url") if isinstance(attributes.get("url"), str) else None,
            content_checkable=False,
            raw=data,
            attempted_providers=["datacite"],
        )
    except ApiDownError:
        raise
    except (ValueError, KeyError, TypeError, IndexError):
        return VerificationResult(
            found=False, provider="datacite", attempted_providers=["datacite"]
        )
