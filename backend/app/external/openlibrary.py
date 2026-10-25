"""Open Library client (F5.6): book existence by ISBN or title. Always
`content_checkable=False` (ticket AC: "books/paywalled = existence-only +
'content not checkable'") — there is no way to verify a cited book's
actual content through a metadata API, only that the book exists.

Shapes confirmed LIVE 2026-08-06: `/isbn/{isbn}.json` 302-redirects to the
real edition record. The shared helper follows HTTPX-generated redirect
requests explicitly so each receives rate admission. `/search.json`
returns `{"docs": [{"title": ..., ...}]}`.
"""

import re

from app.config import Settings
from app.external.http import get_json
from app.external.schemas import VerificationResult
from app.rate_governor import RateGovernor

_governor: RateGovernor | None = None


def _get_governor(settings: Settings) -> RateGovernor:
    global _governor
    rate = settings.openlibrary_rps
    if not _identified_contact(settings):
        rate = min(rate, settings.openlibrary_unidentified_rps)
    interval = 1 / rate
    rpm = max(1, round(rate * 60))
    if _governor is None:
        _governor = RateGovernor(rpm=rpm, min_interval_seconds=interval)
    else:
        # Keep one admission history across settings profiles. A stricter
        # profile may tighten the process, never create a new burst allowance.
        _governor.tighten_min_interval(interval)
        _governor.rpm = min(_governor.rpm, rpm)
    return _governor


def _identified_contact(settings: Settings) -> str | None:
    if not settings.openlibrary_identified_requests:
        return None
    contact = settings.external_contact_email.strip()
    if not contact.isascii() or any(
        ord(character) < 33 or ord(character) == 127 for character in contact
    ):
        return None
    if re.fullmatch(r"[^\s@<>(),;:\\]+@[^\s@<>(),;:\\]+\.[^\s@<>(),;:\\]+", contact) is None:
        return None
    local, domain = contact.rsplit("@", 1)
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return None
    labels = domain.split(".")
    if any(
        not label or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label) is None
        for label in labels
    ):
        return None
    domain = domain.lower()
    reserved = (
        value.lower().strip().strip(".")
        for value in settings.openlibrary_placeholder_contact_domains
    )
    if any(domain == value or domain.endswith("." + value) for value in reserved if value):
        return None
    return contact


def _headers(settings: Settings) -> dict[str, str]:
    contact = _identified_contact(settings)
    identity = f" (mailto:{contact})" if contact else ""
    return {"User-Agent": f"VERIDICAL/1.0{identity}"}


async def lookup_isbn(client, isbn: str, *, settings: Settings) -> VerificationResult:
    governor = _get_governor(settings)
    data = await get_json(
        client,
        f"https://openlibrary.org/isbn/{isbn}.json",
        provider="openlibrary",
        governor=governor,
        max_retries=settings.external_max_retries,
        retry_base_seconds=settings.external_retry_base_seconds,
        headers=_headers(settings),
        pace_redirects=True,
    )
    if data is None:
        return VerificationResult(found=False, provider="openlibrary")
    return VerificationResult(
        found=True,
        provider="openlibrary",
        title=data.get("title"),
        content_checkable=False,
        url=f"https://openlibrary.org{data.get('key', '')}",
        raw=data,
    )


async def search_title(client, title: str, *, settings: Settings) -> VerificationResult:
    governor = _get_governor(settings)
    data = await get_json(
        client,
        "https://openlibrary.org/search.json",
        provider="openlibrary",
        governor=governor,
        max_retries=settings.external_max_retries,
        retry_base_seconds=settings.external_retry_base_seconds,
        params={"title": title, "limit": "1"},
        headers=_headers(settings),
        pace_redirects=True,
    )
    docs = (data or {}).get("docs") or []
    if not docs:
        return VerificationResult(found=False, provider="openlibrary")
    doc = docs[0]
    return VerificationResult(
        found=True,
        provider="openlibrary",
        title=doc.get("title"),
        content_checkable=False,
        url=f"https://openlibrary.org{doc.get('key', '')}",
        raw=doc,
    )
