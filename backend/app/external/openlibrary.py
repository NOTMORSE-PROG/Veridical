"""Open Library client (F5.6): book existence by ISBN or title. Always
`content_checkable=False` (ticket AC: "books/paywalled = existence-only +
'content not checkable'") — there is no way to verify a cited book's
actual content through a metadata API, only that the book exists.

Shapes confirmed LIVE 2026-08-06: `/isbn/{isbn}.json` 302-redirects to the
real edition record (`follow_redirects=True` required on the client —
`app.external.http.build_http_client` sets it); `/search.json` returns
`{"docs": [{"title": ..., ...}]}`.
"""

from app.config import Settings
from app.external.http import get_json
from app.external.schemas import VerificationResult
from app.rate_governor import RateGovernor

_governor: RateGovernor | None = None


def _get_governor(settings: Settings) -> RateGovernor:
    global _governor
    if _governor is None:
        _governor = RateGovernor(rpm=max(1, round(settings.openlibrary_rps * 60)))
    return _governor


def _headers(settings: Settings) -> dict[str, str]:
    # An identifying User-Agent is what earns the 3 rps "identified" tier
    # instead of the 1 rps default (RESEARCH.md §2).
    return {"User-Agent": f"VERIDICAL/1.0 (mailto:{settings.external_contact_email})"}


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
