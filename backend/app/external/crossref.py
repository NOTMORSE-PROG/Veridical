"""CrossRef client (F5.6): DOI existence + retraction/correction status.

Field shapes verified LIVE 2026-08-06 against real records (not assumed
from docs, which don't publish an example): a retracted work's `update-to`
entry has `type: "retraction"`; corrections/errata are `type: "correction"`
or `type: "erratum"` — distinct on purpose (ticket edge case: "a
correction is info-severity not high"). Confirmed both cases exist with
real DOIs via `https://api.crossref.org/v1/works?filter=update-type:retraction`
and `...:correction`. Also confirmed a real, well-known retraction predating
Crossref's 2023 Retraction Watch integration (the 1998 Wakefield MMR paper,
DOI 10.1016/S0140-6736(97)11096-0) carries an empty `update-to`/`relation` —
its title is prefixed "RETRACTED:" instead. `_parse_work` checks both, so
older retractions aren't silently missed just because their metadata
predates the structured field.
"""

from typing import Any

from app.config import Settings
from app.external.http import get_json
from app.external.schemas import VerificationResult
from app.rate_governor import RateGovernor

_single_governor: RateGovernor | None = None
_list_governor: RateGovernor | None = None


def _governors(settings: Settings) -> tuple[RateGovernor, RateGovernor]:
    # Process-wide singletons: the sliding window must persist across
    # calls in one process (same reasoning as app/llm/__init__.py's).
    global _single_governor, _list_governor
    if _single_governor is None:
        _single_governor = RateGovernor(rpm=max(1, round(settings.crossref_single_rps * 60)))
    if _list_governor is None:
        _list_governor = RateGovernor(rpm=max(1, round(settings.crossref_list_rps * 60)))
    return _single_governor, _list_governor


def _headers(settings: Settings) -> dict[str, str]:
    return {"User-Agent": f"VERIDICAL/1.0 (mailto:{settings.external_contact_email})"}


def _parse_work(msg: dict[str, Any]) -> VerificationResult:
    title_list = msg.get("title") or []
    title = title_list[0] if title_list else None
    retracted = False
    is_correction = False
    retraction_detail: str | None = None
    for rel in msg.get("update-to") or []:
        rel_type = (rel.get("type") or "").lower()
        if rel_type == "retraction":
            retracted = True
            label = rel.get("label", "Retraction")
            source = rel.get("source", "unknown")
            retraction_detail = f"{label} (source: {source})"
        elif rel_type in ("correction", "erratum"):
            is_correction = True
    if not retracted and title and title.upper().startswith("RETRACTED"):
        # Pre-2023 retractions predating Crossref's Retraction Watch
        # integration often carry no structured `update-to` at all — the
        # title prefix is the only signal (verified live, module docstring).
        retracted = True
        retraction_detail = "Title is prefixed 'RETRACTED' (no structured update-to metadata)"
    return VerificationResult(
        found=True,
        provider="crossref",
        title=title,
        retracted=retracted,
        retraction_detail=retraction_detail,
        is_correction=is_correction,
        url=msg.get("URL"),
        raw=msg,
    )


async def lookup_doi(client, doi: str, *, settings: Settings) -> VerificationResult:
    governor, _ = _governors(settings)
    data = await get_json(
        client,
        f"https://api.crossref.org/works/{doi}",
        provider="crossref",
        governor=governor,
        max_retries=settings.external_max_retries,
        retry_base_seconds=settings.external_retry_base_seconds,
        params={"mailto": settings.external_contact_email},
        headers=_headers(settings),
    )
    if data is None:
        return VerificationResult(found=False, provider="crossref")
    return _parse_work(data["message"])


async def search_by_title(client, title: str, *, settings: Settings) -> VerificationResult:
    """Metadata search fallback when no DOI is available (ticket
    responsibilities: "else metadata search CrossRef→S2")."""
    _, governor = _governors(settings)
    data = await get_json(
        client,
        "https://api.crossref.org/works",
        provider="crossref",
        governor=governor,
        max_retries=settings.external_max_retries,
        retry_base_seconds=settings.external_retry_base_seconds,
        params={
            "query.bibliographic": title,
            "rows": "1",
            "mailto": settings.external_contact_email,
        },
        headers=_headers(settings),
    )
    items = ((data or {}).get("message") or {}).get("items") or []
    if not items:
        return VerificationResult(found=False, provider="crossref")
    return _parse_work(items[0])
