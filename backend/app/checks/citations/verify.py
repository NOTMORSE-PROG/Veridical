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
    numeric_linkage_flags,
    orphan_flags,
    uncited_flags,
)
from app.checks.citations.numbered import (
    NumberedCitationAssessment,
    analyze_numbered_citations,
    explicit_reference_label,
)
from app.checks.citations.support import (
    ClaimSupportInput,
    ClaimSupportResult,
    run_claim_support_check,
)
from app.checks.source_quality import persist_source_limitation, source_limitation_detail
from app.config import Settings
from app.errors import ApiDownError
from app.external import cache
from app.external import crossref as crossref_client
from app.external import gbooks as gbooks_client
from app.external import openlibrary as openlibrary_client
from app.external import s2 as s2_client
from app.external.schemas import VerificationResult
from app.ingest.patterns import HeadingPatterns
from app.ingest.references import (
    extract_references,
    non_reference_blocks,
    reference_entry_locations,
    reference_reading_order_issues,
    reference_section_page,
    select_references,
)
from app.ingest.schemas import ExtractionResult
from app.llm.base import LLMClient
from app.models.citation import Citation
from app.models.enums import CheckKind, CitationParseStatus, FlagSeverity, ResultOutcome
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


def _numeric_link_evidence(assessment: NumberedCitationAssessment) -> list[dict[str, object]]:
    """One source span per possibly linked reference, bounded by list size.

    This is audit detail, not a claim that a shape-only mark semantically
    cites the source. Original extraction-block index plus character offset
    can reconstruct the exact mark even after furniture filtering.
    """
    evidence_by_reference: dict[int, dict[str, object]] = {}
    for mark, references in assessment.linked:
        for ref in references:
            existing = evidence_by_reference.get(ref.order_index)
            if existing is not None and (
                existing["strong_citation_context"] or not mark.strong_citation_context
            ):
                continue
            evidence_by_reference[ref.order_index] = {
                "reference_order_index": ref.order_index,
                "source_number": explicit_reference_label(ref.raw_text),
                "source_block_index": mark.block_index,
                "source_offset": mark.start,
                "page": mark.page,
                "paragraph": mark.paragraph,
                "raw_mark": mark.raw_text,
                "strong_citation_context": mark.strong_citation_context,
            }
    return list(evidence_by_reference.values())


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
            if not result.found and citation.title:
                result = await gbooks_client.search_title(client, citation.title, settings=settings)
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
    source_limit = source_limitation_detail(extraction)
    selection = select_references(extraction, patterns)
    metadata_only = False
    if source_limit is not None:
        # Metadata verification has a bounded source: the complete bibliography.
        # Admit it only when both source boundaries/provenance and the persisted
        # entries agree. No body fragments are joined across unsupported pages.
        drafts = extract_references(extraction, patterns)
        source_fields = (
            "order_index",
            "raw_text",
            "authors",
            "title",
            "year",
            "doi",
            "isbn",
            "venue",
            "url",
            "parse_status",
        )
        metadata_only = bool(
            drafts
            and len(drafts) == len(citations)
            and not selection.issues
            and not reference_reading_order_issues(extraction, selection)
            and all(
                all(getattr(draft, field) == getattr(citation, field) for field in source_fields)
                for draft, citation in zip(drafts, citations, strict=True)
            )
        )
        if not metadata_only:
            return await persist_source_limitation(
                session, check_run_id, CheckKind.citation_integrity, extraction
            )
    body_blocks = [] if metadata_only else non_reference_blocks(extraction, patterns)
    gaps: set[str] = set()
    in_text = extract_in_text_citations(body_blocks, settings=settings, coverage_gaps=gaps)
    cross = cross_match(in_text, citations)
    numeric = analyze_numbered_citations(
        body_blocks,
        citations,
        max_range_span=settings.numeric_citation_max_range_span,
        context_file=settings.numeric_citation_context_file,
        source_block_index_by_id={id(block): i for i, block in enumerate(extraction.blocks)},
    )
    numbered = numeric.n_numbered_reference_entries > 0
    absence_candidates = numeric.confidently_uncited if numbered else cross.confidently_uncited
    # No supported body style or a partial scan cannot prove a reference unused.
    if metadata_only or gaps or selection.issues or (not numbered and not cross.linked):
        absence_candidates = []
    linkage_unassessed = (
        len(numeric.absence_unassessed_references)
        if numbered
        else len(
            {
                ref.order_index
                for ref in (cross.possibly_cited + (cross.uncited if not cross.linked else []))
            }
        )
    )
    if metadata_only or gaps or selection.issues:
        linkage_unassessed = max(linkage_unassessed, len(citations))
    metadata_unassessed = []
    metadata_api_down = 0
    metadata_not_found = 0
    verification_records = []
    usable_metadata = all(
        c.parse_status == CitationParseStatus.parsed and c.authors and c.year is not None
        for c in citations
    )

    # First linked claim sentence per reference, for V-030's claim-support
    # pairing — a citation mentioned in-text more than once keeps whichever
    # mention was linked first; picking one is enough to check the source
    # actually says what THIS manuscript uses it for.
    claim_by_order_index: dict[int, str] = {}
    for in_text_cite, matched_refs in cross.linked:
        for ref in matched_refs:
            claim_by_order_index.setdefault(ref.order_index, in_text_cite.claim_sentence)
    content_unassessed: set[int] = set()
    for mark, matched_refs in numeric.linked:
        if mark.strong_citation_context and not mark.excerpt_cropped and len(matched_refs) == 1:
            claim_by_order_index.setdefault(matched_refs[0].order_index, mark.claim_sentence)
        else:
            content_unassessed.update(ref.order_index for ref in matched_refs)

    flag_drafts: list[CitationFlagDraft] = (
        numeric_linkage_flags(numeric.orphan_marks, absence_candidates, len(citations))
        if numbered
        else [
            *(orphan_flags(cross.orphans) if usable_metadata else []),
            *uncited_flags(absence_candidates),
        ]
    )
    if metadata_only or selection.issues or not citations:
        flag_drafts = []
    claim_support_pairs: list[ClaimSupportInput] = []
    locations = {
        (location.index, location.raw_text): location
        for location in reference_entry_locations(extraction, patterns)
    }
    for citation in citations:
        parsed = citation.parse_status == CitationParseStatus.parsed
        metadata_complete = bool(
            parsed and citation.title and citation.authors and citation.year is not None
        )
        lookup_eligible = parsed and (bool(citation.doi or citation.isbn) or metadata_complete)
        if not metadata_complete:
            metadata_unassessed.append(citation.order_index)
        if not lookup_eligible:
            verification_records.append(
                {
                    "reference_order_index": citation.order_index,
                    "status": "unassessed",
                    "reason": "unsupported_reference_structure",
                    "lookup_attempted": False,
                    "metadata_complete": False,
                }
            )
            continue
        verdict = await verify_citation(session, client, citation, settings=settings)
        metadata_api_down += verdict.kind == VerdictKind.api_down
        metadata_not_found += verdict.kind == VerdictKind.not_found
        verification_records.append(
            {
                "reference_order_index": citation.order_index,
                "status": verdict.kind.value,
                "lookup_attempted": True,
                "lookup_kind": "doi" if citation.doi else "isbn" if citation.isbn else "title",
                "metadata_complete": metadata_complete,
            }
        )
        draft = _verdict_flag_draft(citation, verdict)
        if draft is not None:
            location = locations.get((citation.order_index + 1, citation.raw_text))
            if location is not None:
                anchor = (
                    f"p. {location.page}"
                    if location.page is not None
                    else f"\u00b6{location.paragraph}"
                    if location.paragraph is not None
                    else draft.page_anchor
                )
                draft = replace(draft, page_anchor=anchor)
            flag_drafts.append(draft)
        claim_sentence = claim_by_order_index.get(citation.order_index)
        if claim_sentence and (
            not metadata_complete or verdict.result is None or not verdict.result.abstract
        ):
            content_unassessed.add(citation.order_index)
        if (
            metadata_complete
            and verdict.kind == VerdictKind.existence_confirmed
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
    claim_support_checked = 0
    if llm is not None and claim_support_pairs:
        claim_support_result = await run_claim_support_check(
            llm, claim_support_pairs, check_run_id=check_run_id, settings=settings
        )
        flag_drafts.extend(claim_support_result.flags)
        claim_support_checked = len(claim_support_pairs) - claim_support_result.n_skipped_total
    elif claim_support_pairs:
        content_unassessed.update(pair.citation.order_index for pair in claim_support_pairs)

    # BUG-073: a check that skipped even one pair did NOT fully execute --
    # `passed` is reserved for a run that judged everything it set out to
    # judge. Priority when causes mix (rare, but a chunked run can hit more
    # than one in a single check_run): `unverifiable` first -- a parse
    # failure is D-017's exact defect class and charter rule 9 says it must
    # never hide behind a more benign-sounding cause -- then `api_down`
    # (retrying later plausibly helps), then `quota_exhausted` (the
    # existing, most self-resolving state) last. The real per-cause counts
    # are always in `detail` regardless of which one outcome wins.
    coverage_reason = (
        "source_reading_order_partial"
        if metadata_only
        else "reference_list_unavailable"
        if selection.issues or not citations
        else "reference_metadata_insufficient"
        if metadata_unassessed
        else "author_year_citation_parse_unassessed"
        if gaps
        else "numeric_citation_scan_limit"
        if numeric.scan_limited and numbered
        else "numeric_citation_style_unassessed"
        if numbered and (linkage_unassessed or numeric.linkage_unassessed)
        else "author_year_citation_parse_unassessed"
        if linkage_unassessed or cross.ambiguous
        else "claim_support_unavailable"
        if content_unassessed
        else None
    )
    if coverage_reason:
        outcome = ResultOutcome.escalated
    elif metadata_not_found or claim_support_result.n_skipped_parse_failure > 0:
        outcome = ResultOutcome.unverifiable
    elif metadata_api_down or claim_support_result.n_skipped_api_down > 0:
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
            **(
                {
                    "analysis_coverage": "partial"
                    if any(record["lookup_attempted"] for record in verification_records)
                    else "unsupported",
                    "assessment_scope": "reference_metadata_only",
                    "source_issues": source_limit["source_issues"],
                    "n_source_pages_unassessed": source_limit["n_source_pages_unassessed"],
                    "n_reference_metadata_attempted": sum(
                        record["lookup_attempted"] for record in verification_records
                    ),
                }
                if metadata_only
                else {}
            ),
            "n_references": len(citations),
            "n_in_text_citations": len(in_text),
            "n_linked": len(cross.linked),
            "n_orphans": len(cross.orphans),
            "n_uncited": len(absence_candidates),
            "coverage_reason": coverage_reason,
            "n_metadata_skipped_insufficient": len(metadata_unassessed),
            "n_linkage_unassessed": linkage_unassessed,
            "n_metadata_api_down": metadata_api_down,
            "n_metadata_not_found": metadata_not_found,
            "n_claim_support_unassessed": len(content_unassessed),
            "n_numeric_candidate_links": len(numeric.linked),
            "n_numeric_assessed_links": sum(m.strong_citation_context for m, _ in numeric.linked),
            "numeric_link_evidence": _numeric_link_evidence(numeric),
            "reference_metadata_coverage": verification_records,
            "reference_list_page": reference_section_page(extraction, patterns),
            "reference_selection_issues": list(selection.issues),
            "citation_parse_issues": sorted(gaps),
            "n_claim_support_checked": claim_support_checked,
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
    # (backend review finding, same reconstructability reasoning already
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
