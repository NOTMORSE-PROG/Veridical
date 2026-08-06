"""Semantic Scholar client (F5.6): DOI/title existence + metadata
(abstract, open-access PDF URL) — the fallback existence source when
CrossRef doesn't have a DOI match, and V-030's future abstract-retrieval
source (kept in `raw`, not re-fetched separately).

`/paper/DOI:{doi}` shape confirmed LIVE 2026-08-06 (flat object, no
wrapper). `/paper/search`'s `{"data": [...]}` wrapper is per the official
docs (semanticscholar.org/product/api) — NOT independently re-confirmed
live this session: a second live call hit the same shared unauthenticated
429 the single-DOI call above had already burned into (an honest, disclosed
gap, not assumed silently). S2 has no retraction concept in its API — this
client never sets `retracted`/`is_correction`; the retraction check is
CrossRef-only (V-029).
"""

from app.config import Settings
from app.external.http import get_json
from app.external.schemas import VerificationResult
from app.rate_governor import RateGovernor

_FIELDS = "title,abstract,externalIds,isOpenAccess,openAccessPdf"

_governor: RateGovernor | None = None


def _get_governor(settings: Settings) -> RateGovernor:
    global _governor
    if _governor is None:
        # Self-throttled regardless of key (module docstring / RESEARCH.md
        # §2): the "5,000/5min unauthenticated" ceiling is real but shared
        # globally, so 1 rps is the good-citizen default either way.
        _governor = RateGovernor(rpm=max(1, round(settings.semantic_scholar_rps * 60)))
    return _governor


def _headers(settings: Settings) -> dict[str, str]:
    key = settings.semantic_scholar_api_key
    return {"x-api-key": key} if key else {}


def _parse_paper(data: dict) -> VerificationResult:
    pdf = data.get("openAccessPdf") or {}
    return VerificationResult(
        found=True,
        provider="s2",
        title=data.get("title"),
        url=pdf.get("url"),
        abstract=data.get("abstract"),
        raw=data,
    )


async def lookup_doi(client, doi: str, *, settings: Settings) -> VerificationResult:
    governor = _get_governor(settings)
    data = await get_json(
        client,
        f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
        provider="s2",
        governor=governor,
        max_retries=settings.external_max_retries,
        retry_base_seconds=settings.external_retry_base_seconds,
        params={"fields": _FIELDS},
        headers=_headers(settings),
    )
    if data is None:
        return VerificationResult(found=False, provider="s2")
    return _parse_paper(data)


async def search_by_title(client, title: str, *, settings: Settings) -> VerificationResult:
    governor = _get_governor(settings)
    data = await get_json(
        client,
        "https://api.semanticscholar.org/graph/v1/paper/search",
        provider="s2",
        governor=governor,
        max_retries=settings.external_max_retries,
        retry_base_seconds=settings.external_retry_base_seconds,
        params={"query": title, "limit": "1", "fields": _FIELDS},
        headers=_headers(settings),
    )
    papers = (data or {}).get("data") or []
    if not papers:
        return VerificationResult(found=False, provider="s2")
    return _parse_paper(papers[0])
