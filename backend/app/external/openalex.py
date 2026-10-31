"""OpenAlex works search (F5.6): title candidates for references Crossref
does not hold, such as arXiv preprints, theses and repository copies.

Checked live 2026-10-31 (help.openalex.org, "Rate limits and authentication"
and "API guide for LLMs"): basic queries need no key; a keyless caller shares
a $0.10 daily budget per network address, a free key gives $1 a day, and a
search costs $0.001, so about 100 keyless searches a day. Over budget the API
answers 429, which the shared request helper reports as a lookup that could
not be completed, never as "not found". The docs no longer describe a polite
pool, so no contact address is sent.

Retraction status is read from Crossref only: an OpenAlex record never sets
`retracted` or `is_correction` here.
"""

import re

from app.config import Settings
from app.external.http import get_json
from app.external.schemas import VerificationResult
from app.rate_governor import RateGovernor

_governor: RateGovernor | None = None

# The title.search filter treats these as filter syntax (separators, OR,
# value delimiters), so they are replaced by spaces before searching. The
# search is word-based, so the words themselves are unchanged.
_FILTER_SYNTAX = re.compile(r"[,|:!()\[\]\"]")
# is_retracted is kept in the stored record for an instructor to read; it is
# not used as a retraction finding (see the module docstring).
_FIELDS = "id,doi,display_name,publication_year,authorships,type,ids,is_retracted"


def _get_governor(settings: Settings) -> RateGovernor:
    global _governor
    if _governor is None:
        _governor = RateGovernor(
            rpm=max(1, round(settings.openalex_rps * 60)),
            min_interval_seconds=1 / settings.openalex_rps,
        )
    return _governor


def _parse_work(item: dict) -> VerificationResult:
    doi = item.get("doi")
    return VerificationResult(
        found=True,
        provider="openalex",
        title=item.get("display_name") if isinstance(item.get("display_name"), str) else None,
        url=doi if isinstance(doi, str) else item.get("id"),
        raw=item,
    )


async def search_title_candidates(
    client, title: str, *, settings: Settings, rows: int
) -> list[VerificationResult]:
    """Up to `rows` best title matches, best first; empty when none."""
    value = " ".join(_FILTER_SYNTAX.sub(" ", title).split())
    if not value:
        return []
    params = {"filter": f"title.search:{value}", "per_page": str(rows), "select": _FIELDS}
    if settings.openalex_api_key:
        params["api_key"] = settings.openalex_api_key
    data = await get_json(
        client,
        f"{settings.openalex_base_url.rstrip('/')}/works",
        provider="openalex",
        governor=_get_governor(settings),
        max_retries=settings.external_max_retries,
        retry_base_seconds=settings.external_retry_base_seconds,
        params=params,
    )
    items = (data or {}).get("results")
    if not isinstance(items, list):
        return []
    return [_parse_work(item) for item in items[:rows] if isinstance(item, dict)]
