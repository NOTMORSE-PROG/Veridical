"""Existence + retraction checks (F5.2/F5.3/F5.5, V-029) — the F5
citation-integrity assembly: combines V-027's in-text cross-match (orphan/
uncited) with real external verification (V-028's CrossRef/S2/OpenLibrary/
GBooks clients) and V-030's claim-support check into one `check_result`
per run, with real `Flag` rows.

Wording discipline is enforced here, not left to callers (charter rule 3):
"unverifiable — please check manually", NEVER "fake"; a retraction is the
ONLY high-severity outcome this check ever produces — everything else
(correction, not-found, transient outage) is low severity, because none of
those are, by themselves, evidence of anything wrong with the manuscript.
"""

from dataclasses import dataclass, replace
from enum import StrEnum

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_event
from app.checks.citations.extract import (
    CitationFlagDraft,
    cross_match,
    extract_in_text_citations,
    orphan_flags,
    uncited_flags,
)
from app.checks.citations.support import (
    ClaimSupportInput,
    ClaimSupportResult,
    run_claim_support_check,
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
from app.llm.base import LLMClient
from app.models.citation import Citation
from app.models.enums import CheckKind, FlagSeverity, ResultOutcome
from app.models.run import CheckResult, Flag


class VerdictKind(StrEnum):
    existence_confirmed = "existence_confirmed"
    retracted = "retracted"
    corrected = "corrected"
    not_found = "not_found"
    api_down = "api_down"
    # BUG-078/FEATURES.md §9: a `not_found` result whose citation_cache row
    # an instructor has since manually confirmed legitimate (e.g. a real
    # local/Philippine source these providers don't index) -- silence, same
    # as `existence_confirmed`, never re-flagged.
    instructor_confirmed = "instructor_confirmed"


@dataclass(frozen=True)
class CitationVerdict:
    kind: VerdictKind
    result: VerificationResult | None = None
    # Set only when a real lookup key exists (doi/isbn/title) -- the
    # "no DOI/ISBN/title at all" `not_found` case has nothing to confirm,
    # so stays None; `_verdict_flag_draft` uses this to decide whether the
    # resulting flag is confirmable (BUG-078).
    key_kind: str | None = None
    key_value: str | None = None


RETRACTED_WORDING = (
    "This source appears to have been RETRACTED ({detail}). Possible "
    "integrity concern, please verify with the publisher or DOI before "
    "the defense."
)
CORRECTED_WORDING = (
    "This source has a published correction/erratum on record ({detail}). "
    "Not a retraction, for your awareness."
)
UNVERIFIABLE_NOT_FOUND_WORDING = (
    "Could not find this source in CrossRef, Semantic Scholar, Open "
    "Library, or Google Books. Possible local/unindexed source, a typo in "
    "the citation, or a source these providers don't cover, please check "
    "manually. Unverifiable, not necessarily incorrect."
)
UNVERIFIABLE_API_DOWN_WORDING = (
    "Could not check this source right now, the verification service(s) "
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
        verdict = await _verify_keyed(
            session,
            "doi",
            citation.doi,
            settings=settings,
            fetch=lambda: crossref_client.lookup_doi(client, citation.doi, settings=settings),
        )
        # CrossRef often has no abstract (many publishers don't submit one) —
        # most real DOI-backed citations would never reach V-030's
        # claim-support check without this. S2 is the more reliable
        # abstract source; only queried when existence is already confirmed
        # and CrossRef didn't supply one, cached under its own key so it
        # never clobbers the CrossRef-sourced existence/retraction result.
        if (
            verdict.kind == VerdictKind.existence_confirmed
            and verdict.result is not None
            and not verdict.result.abstract
        ):
            abstract = await _fetch_supplementary_abstract(
                session, client, citation.doi, settings=settings
            )
            if abstract:
                verdict = CitationVerdict(verdict.kind, replace(verdict.result, abstract=abstract))
        return verdict

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


async def _fetch_supplementary_abstract(
    session: AsyncSession, client: httpx.AsyncClient, doi: str, *, settings: Settings
) -> str | None:
    cached = await cache.get_cached(
        session,
        key_kind="doi_abstract",
        key_value=doi,
        stale_days=settings.citation_cache_stale_days,
    )
    if cached is not None:
        return cached.abstract
    try:
        result = await s2_client.lookup_doi(client, doi, settings=settings)
    except ApiDownError:
        return None
    await cache.store_result(
        session, key_kind="doi_abstract", key_value=doi, provider="s2", result=result
    )
    return result.abstract


async def _verify_keyed(session, key_kind, key_value, *, settings, fetch) -> CitationVerdict:
    cached = await cache.get_cached(
        session,
        key_kind=key_kind,
        key_value=key_value,
        stale_days=settings.citation_cache_stale_days,
    )
    if cached is not None:
        verdict = _verdict_from_result(cached, key_kind=key_kind, key_value=key_value)
    else:
        try:
            result = await fetch()
        except ApiDownError:
            return CitationVerdict(VerdictKind.api_down)
        await cache.store_result(
            session, key_kind=key_kind, key_value=key_value, provider=result.provider, result=result
        )
        verdict = _verdict_from_result(result, key_kind=key_kind, key_value=key_value)
    if verdict.kind == VerdictKind.not_found and await cache.is_instructor_confirmed(
        session, key_kind=key_kind, key_value=key_value
    ):
        return CitationVerdict(
            VerdictKind.instructor_confirmed, verdict.result, key_kind, key_value
        )
    return verdict


def _verdict_from_result(
    result: VerificationResult, *, key_kind: str | None = None, key_value: str | None = None
) -> CitationVerdict:
    if not result.found:
        return CitationVerdict(VerdictKind.not_found, result, key_kind, key_value)
    if result.retracted:
        return CitationVerdict(VerdictKind.retracted, result)
    if result.is_correction:
        return CitationVerdict(VerdictKind.corrected, result)
    return CitationVerdict(VerdictKind.existence_confirmed, result)


def _verdict_flag_draft(citation: Citation, verdict: CitationVerdict) -> CitationFlagDraft | None:
    if verdict.kind in (VerdictKind.existence_confirmed, VerdictKind.instructor_confirmed):
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
    detail = {"kind": "unverifiable_not_found", "reason": UNVERIFIABLE_NOT_FOUND_WORDING}
    if verdict.key_kind is not None:
        # BUG-078: only present when there's a real DOI/ISBN/title to key a
        # confirmation on -- absent for the "no identifier at all" case, so
        # the frontend/`confirm_citation_source` can tell confirmable
        # flags apart from ones with nothing to confirm.
        detail["key_kind"] = verdict.key_kind
        detail["key_value"] = verdict.key_value
    return CitationFlagDraft(  # not_found
        severity=FlagSeverity.low,
        evidence_excerpt=citation.raw_text,
        page_anchor=anchor,
        detail=detail,
    )


async def run_citation_integrity_check(
    session: AsyncSession,
    client: httpx.AsyncClient,
    check_run_id: int,
    citations: list[Citation],
    extraction: ExtractionResult,
    patterns: HeadingPatterns,
    settings: Settings,
    llm: LLMClient | None = None,
) -> CheckResult:
    body_blocks = non_reference_blocks(extraction, patterns)
    in_text = extract_in_text_citations(body_blocks)
    cross = cross_match(in_text, citations)

    # First linked claim sentence per reference, for V-030's claim-support
    # pairing — a citation mentioned in-text more than once keeps whichever
    # mention was linked first; picking one is enough to check the source
    # actually says what THIS manuscript uses it for.
    claim_by_order_index: dict[int, str] = {}
    for in_text_cite, matched_refs in cross.linked:
        for ref in matched_refs:
            claim_by_order_index.setdefault(ref.order_index, in_text_cite.claim_sentence)

    flag_drafts: list[CitationFlagDraft] = [
        *orphan_flags(cross.orphans),
        *uncited_flags(cross.uncited),
    ]
    claim_support_pairs: list[ClaimSupportInput] = []
    for citation in citations:
        verdict = await verify_citation(session, client, citation, settings=settings)
        draft = _verdict_flag_draft(citation, verdict)
        if draft is not None:
            flag_drafts.append(draft)
        claim_sentence = claim_by_order_index.get(citation.order_index)
        if (
            verdict.kind == VerdictKind.existence_confirmed
            and verdict.result is not None
            and verdict.result.abstract
            and claim_sentence
        ):
            claim_support_pairs.append(
                ClaimSupportInput(
                    citation=citation,
                    claim_sentence=claim_sentence,
                    abstract=verdict.result.abstract,
                )
            )

    claim_support_result = ClaimSupportResult([], 0, 0, 0)
    if llm is not None and claim_support_pairs:
        claim_support_result = await run_claim_support_check(
            llm, claim_support_pairs, check_run_id=check_run_id, settings=settings
        )
        flag_drafts.extend(claim_support_result.flags)

    # BUG-073: a check that skipped even one pair did NOT fully execute --
    # `passed` is reserved for a run that judged everything it set out to
    # judge. Priority when causes mix (rare, but a chunked run can hit more
    # than one in a single check_run): `unverifiable` first -- a parse
    # failure is D-017's exact defect class and charter rule 9 says it must
    # never hide behind a more benign-sounding cause -- then `api_down`
    # (retrying later plausibly helps), then `quota_exhausted` (the
    # existing, most self-resolving state) last. The real per-cause counts
    # are always in `detail` regardless of which one outcome wins.
    if claim_support_result.n_skipped_parse_failure > 0:
        outcome = ResultOutcome.unverifiable
    elif claim_support_result.n_skipped_api_down > 0:
        outcome = ResultOutcome.api_down
    elif claim_support_result.n_skipped_quota > 0:
        outcome = ResultOutcome.quota_exhausted
    else:
        outcome = ResultOutcome.passed

    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=None,
        kind=CheckKind.citation_integrity,
        outcome=outcome,
        detail={
            "n_references": len(citations),
            "n_in_text_citations": len(in_text),
            "n_linked": len(cross.linked),
            "n_orphans": len(cross.orphans),
            "n_uncited": len(cross.uncited),
            "n_claim_support_checked": len(claim_support_pairs)
            - claim_support_result.n_skipped_total,
            "n_claim_support_pairs_total": len(claim_support_pairs),
            "n_claim_support_skipped_quota": claim_support_result.n_skipped_quota,
            "n_claim_support_skipped_api_down": claim_support_result.n_skipped_api_down,
            "n_claim_support_skipped_parse_failure": claim_support_result.n_skipped_parse_failure,
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
                detail=draft.detail,
            )
        )
    # BUG-151: a check-level summary event, distinct from whatever per-
    # source `llm_call`/external-provider audit trail already exists --
    # see `checks/reuse/service.py`'s sibling call for the same fix
    # applied to F7 (charter judgment 4). `citation_cache_stale_days`
    # decides whether a cached verification is trusted or re-fetched
    # (backend-critic finding, same reconstructability reasoning already
    # applied to F4's thresholds: this is env-configurable, so a past
    # verdict needs its own record of what was in force).
    await write_audit_event(
        session,
        event_type="citation_integrity_check_computed",
        check_run_id=check_run_id,
        payload={
            **result.detail,
            "outcome": result.outcome.value,
            "thresholds": {"cache_stale_days": settings.citation_cache_stale_days},
        },
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
