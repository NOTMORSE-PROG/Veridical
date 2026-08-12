"""CrossRef client (F5.6): DOI existence + retraction/correction status.

Field shapes verified LIVE 2026-08-06 against real records (not assumed
from docs, which don't publish an example): a retracted work's `update-to`
entry has `type: "retraction"`; corrections/errata are `type: "correction"`
or `type: "erratum"` — distinct on purpose (ticket edge case: "a
correction is info-severity not high"). Confirmed both cases exist with
real DOIs via `https://api.crossref.org/v1/works?filter=update-type:retraction`
and `...:correction`.

BUG-034 (2026-08-13, re-verified live against the same DOI): the 1998
Wakefield MMR paper's real retraction data lives ENTIRELY under
`updated-by`, not `update-to` — `update-to` is null. `updated-by` uses
the identical relation-type vocabulary (`type: "retraction"` /
`"correction"` / `"erratum"`) and is CrossRef's Retraction Watch
integration's actual field (`source: "retraction-watch"` on every
entry) — `update-to` is the older, publisher-submitted mechanism.
`_parse_work` checks both fields now; before this fix it only read
`update-to`, so this exact DOI was only ever caught by the title-prefix
fallback below — incidental (Elsevier/Lancet happened to also prefix
the title), not because the structured path was actually complete. A
retracted paper without a "RETRACTED:" title prefix AND with its data
only under `updated-by` would have been missed entirely.
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
    # BUG-034: `update-to` (publisher-submitted) and `updated-by`
    # (CrossRef's Retraction Watch integration) both carry retraction
    # relations under the same vocabulary — a real record can have its
    # ONLY structured retraction data under either one, so both must be
    # read, not just `update-to`. `backend-critic` review finding: real
    # DOIs exist with the SAME retraction duplicated (or even a distinct
    # second retraction notice) under both fields — which one's detail
    # string gets shown must be an explicit choice, not an accident of
    # iteration order. `retraction-watch`-sourced entries win ties: it's
    # CrossRef's actively-maintained integration and this module's own
    # docstring already claims it's "the superset in practice."
    _RETRACTION_WATCH_PRIORITY = 1
    _OTHER_SOURCE_PRIORITY = 0
    best_priority = -1
    for rel in [*(msg.get("update-to") or []), *(msg.get("updated-by") or [])]:
        rel_type = (rel.get("type") or "").lower()
        if rel_type == "retraction":
            source = rel.get("source", "unknown")
            priority = (
                _RETRACTION_WATCH_PRIORITY
                if source == "retraction-watch"
                else _OTHER_SOURCE_PRIORITY
            )
            if priority >= best_priority:
                retracted = True
                best_priority = priority
                label = rel.get("label", "Retraction")
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
