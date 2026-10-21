"""Public exact-DOI DataCite metadata. Existence is not claim support."""

from functools import lru_cache
from urllib.parse import quote

from app.errors import ApiDownError
from app.external.http import get_json
from app.external.schemas import VerificationResult
from app.rate_governor import RateGovernor


@lru_cache
def _governor(rpm):
    return RateGovernor(rpm=rpm)


async def lookup_doi(client, doi, *, settings):
    try:
        data = await get_json(
            client,
            f"{settings.datacite_base_url.rstrip('/')}/dois/{quote(doi, safe='')}",
            provider="datacite",
            governor=_governor(settings.datacite_rpm),
            max_retries=settings.external_max_retries,
            retry_base_seconds=settings.external_retry_base_seconds,
            headers={"User-Agent": f"VERIDICAL/1.0 (mailto:{settings.external_contact_email})"},
        )
        if data is None:
            return VerificationResult(
                found=False, provider="datacite", attempted_providers=["datacite"]
            )
        record = data["data"]
        attributes = record["attributes"]
        identifier = attributes["doi"]
        titles = attributes["titles"]
        if (
            record["type"] != "dois"
            or not isinstance(identifier, str)
            or record["id"] != identifier
            or not isinstance(titles, list)
            or not titles
            or not isinstance(titles[0]["title"], str)
        ):
            raise ValueError("Incomplete registry metadata")
        # The full original public payload is retained. No abstract is inferred
        # from a title, description, caption or related identifier.
        return VerificationResult(
            found=True,
            provider="datacite",
            title=titles[0]["title"],
            url=attributes.get("url"),
            content_checkable=False,
            raw=data,
            attempted_providers=["datacite"],
        )
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise ApiDownError("DataCite returned metadata that could not be validated.") from exc
