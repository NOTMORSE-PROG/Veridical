"""Existence + retraction checks (F5.2/F5.3/F5.5, V-029) — the F5
citation-integrity assembly: combines V-027's in-text cross-match (orphan/
uncited) with real external verification (V-028's CrossRef/S2/OpenLibrary/
GBooks clients) into one `check_result` per run, with real `Flag` rows.

Wording discipline is enforced here, not left to callers (charter rule 3):
"unverifiable — please check manually", NEVER "fake"; a retraction is the
ONLY high-severity outcome this check ever produces — everything else
(correction, not-found, transient outage) is low severity, because none of
those are, by themselves, evidence of anything wrong with the manuscript.
"""

from dataclasses import dataclass
from enum import StrEnum

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.citations.extract import (
    CitationFlagDraft,
    cross_match,
    extract_in_text_citations,
    orphan_flags,
    uncited_flags,
)
from app.config import Settings
from app.errors import ApiDownError
from app.external import cache
from app.external import crossref as crossref_client
from app.external import gbooks as gbooks_client
from app.external import openlibrary as openlibrary_client
from app.external import s2 as s2_client
from app.external.schemas import VerificationResult
from app.ingest.patterns import HeadingPatterns
from app.ingest.references import non_reference_blocks
from app.ingest.schemas import ExtractionResult
from app.models.citation import Citation
from app.models.enums import CheckKind, FlagSeverity, ResultOutcome
from app.models.run import CheckResult, Flag


class VerdictKind(StrEnum):
    existence_confirmed = "existence_confirmed"
    retracted = "retracted"
    corrected = "corrected"
    not_found = "not_found"
    api_down = "api_down"


@dataclass(frozen=True)
class CitationVerdict:
    kind: VerdictKind
    result: VerificationResult | None = None


RETRACTED_WORDING = (
    "This source appears to have been RETRACTED ({detail}). Possible "
    "integrity concern — please verify with the publisher or DOI before "
    "the defense."
)
CORRECTED_WORDING = (
    "This source has a published correction/erratum on record ({detail}). "
    "Not a retraction — for your awareness."
)
UNVERIFIABLE_NOT_FOUND_WORDING = (
    "Could not find this source in CrossRef, Semantic Scholar, Open "
    "Library, or Google Books. Possible local/unindexed source, a typo in "
    "the citation, or a source these providers don't cover — please check "
    "manually. Unverifiable, not necessarily incorrect."
)
UNVERIFIABLE_API_DOWN_WORDING = (
    "Could not check this source right now — the verification service(s) "
    "were unreachable. Re-run this check later to verify."
)


async def verify_citation(
    session: AsyncSession, client: httpx.AsyncClient, citation: Citation, *, settings: Settings
) -> CitationVerdict:
    """Existence: DOI → CrossRef; books (ISBN) → Open Library→Google Books;
    else (no DOI/ISBN) → metadata search CrossRef→Semantic Scholar (ticket
    responsibilities). A DOI that fails to resolve is reported unverifiable
    directly, NOT retried via title search — falling back could match a
    fabricated/mismatched citation to an unrelated real paper by title
    alone, which would be a false reassurance (worse than a false flag,
    charter judgment #1)."""
    if citation.doi:
        return await _verify_keyed(
            session,
            "doi",
            citation.doi,
            settings=settings,
            fetch=lambda: crossref_client.lookup_doi(client, citation.doi, settings=settings),
        )

    if citation.isbn:

        async def _book_lookup() -> VerificationResult:
            result = await openlibrary_client.lookup_isbn(client, citation.isbn, settings=settings)
            if not result.found:
                result = await gbooks_client.search_title(
                    client, citation.title or citation.raw_text, settings=settings
                )
            return result

        return await _verify_keyed(
            session, "isbn", citation.isbn, settings=settings, fetch=_book_lookup
        )

    if citation.title:

        async def _title_search() -> VerificationResult:
            result = await crossref_client.search_by_title(
                client, citation.title, settings=settings
            )
            if not result.found:
                result = await s2_client.search_by_title(client, citation.title, settings=settings)
            return result

        return await _verify_keyed(
            session, "title", citation.title.casefold(), settings=settings, fetch=_title_search
        )

    # No DOI/ISBN/title to key a lookup on (e.g. a parse_failed entry) —
    # genuinely unverifiable, no network call made.
    return CitationVerdict(VerdictKind.not_found)


async def _verify_keyed(session, key_kind, key_value, *, settings, fetch) -> CitationVerdict:
    cached = await cache.get_cached(
        session,
        key_kind=key_kind,
        key_value=key_value,
        stale_days=settings.citation_cache_stale_days,
    )
    if cached is not None:
        return _verdict_from_result(cached)
    try:
        result = await fetch()
    except ApiDownError:
        return CitationVerdict(VerdictKind.api_down)
    await cache.store_result(
        session, key_kind=key_kind, key_value=key_value, provider=result.provider, result=result
    )
    return _verdict_from_result(result)


def _verdict_from_result(result: VerificationResult) -> CitationVerdict:
    if not result.found:
        return CitationVerdict(VerdictKind.not_found, result)
    if result.retracted:
        return CitationVerdict(VerdictKind.retracted, result)
    if result.is_correction:
        return CitationVerdict(VerdictKind.corrected, result)
    return CitationVerdict(VerdictKind.existence_confirmed, result)


def _verdict_flag_draft(citation: Citation, verdict: CitationVerdict) -> CitationFlagDraft | None:
    if verdict.kind == VerdictKind.existence_confirmed:
        return None  # a confirmed, clean source is not a finding — no noise
    anchor = f"reference #{citation.order_index + 1}"
    if verdict.kind == VerdictKind.retracted:
        detail = (verdict.result and verdict.result.retraction_detail) or "retracted"
        return CitationFlagDraft(
            severity=FlagSeverity.high,
            evidence_excerpt=citation.raw_text,
            page_anchor=anchor,
            detail={
                "kind": "retracted_source",
                "reason": RETRACTED_WORDING.format(detail=detail),
                "provider": verdict.result.provider if verdict.result else None,
            },
        )
    if verdict.kind == VerdictKind.corrected:
        return CitationFlagDraft(
            severity=FlagSeverity.low,
            evidence_excerpt=citation.raw_text,
            page_anchor=anchor,
            detail={
                "kind": "corrected_source",
                "reason": CORRECTED_WORDING.format(detail="correction/erratum on record"),
                "provider": verdict.result.provider if verdict.result else None,
            },
        )
    if verdict.kind == VerdictKind.api_down:
        return CitationFlagDraft(
            severity=FlagSeverity.low,
            evidence_excerpt=citation.raw_text,
            page_anchor=anchor,
            detail={"kind": "unverifiable_api_down", "reason": UNVERIFIABLE_API_DOWN_WORDING},
        )
    return CitationFlagDraft(  # not_found
        severity=FlagSeverity.low,
        evidence_excerpt=citation.raw_text,
        page_anchor=anchor,
        detail={"kind": "unverifiable_not_found", "reason": UNVERIFIABLE_NOT_FOUND_WORDING},
    )


async def run_citation_integrity_check(
    session: AsyncSession,
    client: httpx.AsyncClient,
    check_run_id: int,
    citations: list[Citation],
    extraction: ExtractionResult,
    patterns: HeadingPatterns,
    settings: Settings,
) -> CheckResult:
    body_blocks = non_reference_blocks(extraction, patterns)
    in_text = extract_in_text_citations(body_blocks)
    cross = cross_match(in_text, citations)

    flag_drafts: list[CitationFlagDraft] = [
        *orphan_flags(cross.orphans),
        *uncited_flags(cross.uncited),
    ]
    for citation in citations:
        verdict = await verify_citation(session, client, citation, settings=settings)
        draft = _verdict_flag_draft(citation, verdict)
        if draft is not None:
            flag_drafts.append(draft)

    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=None,
        kind=CheckKind.citation_integrity,
        outcome=ResultOutcome.passed,
        detail={
            "n_references": len(citations),
            "n_in_text_citations": len(in_text),
            "n_linked": len(cross.linked),
            "n_orphans": len(cross.orphans),
            "n_uncited": len(cross.uncited),
            "n_flags": len(flag_drafts),
        },
    )
    session.add(result)
    await session.flush()  # need result.id before attaching flags
    for draft in flag_drafts:
        session.add(
            Flag(
                check_result_id=result.id,
                severity=draft.severity,
                evidence_excerpt=draft.evidence_excerpt,
                page_anchor=draft.page_anchor,
            )
        )
    await session.commit()
    return result


async def existing_citation_integrity_result(
    session: AsyncSession, check_run_id: int
) -> CheckResult | None:
    """Idempotency guard (same contract every other stage keeps, ENGINEERING
    §4): a resumed run must not re-verify (and re-spend provider calls on)
    citations it already checked."""
    return await session.scalar(
        select(CheckResult).where(
            CheckResult.check_run_id == check_run_id,
            CheckResult.kind == CheckKind.citation_integrity,
        )
    )
