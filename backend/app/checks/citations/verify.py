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

import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from zoneinfo import ZoneInfoNotFoundError

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
from app.checks.citations.linkage_guard import guarded_absence_candidates
from app.checks.citations.metadata import (
    MetadataComparison,
    compare_metadata,
    returned_doi,
    same_doi,
)
from app.checks.citations.numbered import (
    NumberedCitationAssessment,
    analyze_numbered_citations,
    explicit_reference_label,
)
from app.checks.citations.projection import SOURCE_FIELDS, project_quoted_metadata
from app.checks.citations.recency import (
    RecencyAssessment,
    assess_recency,
    check_year_of,
    load_grammar,
    off_summary,
    resolve_policy,
    unassessed_summary,
)
from app.checks.citations.source_locator import with_reference_locator
from app.checks.citations.support import (
    ClaimSupportInput,
    ClaimSupportResult,
    run_claim_support_check,
)
from app.checks.citations.title_policy import TitlePolicy, load_title_policy
from app.checks.source_quality import persist_source_limitation, source_limitation_detail
from app.config import Settings
from app.errors import ApiDownError
from app.external import cache
from app.external import crossref as crossref_client
from app.external import datacite as datacite_client
from app.external import gbooks as gbooks_client
from app.external import openlibrary as openlibrary_client
from app.external import s2 as s2_client
from app.external.schemas import VerificationResult
from app.ingest.analytical_source import analytical_source
from app.ingest.citation_dates import reference_date_key
from app.ingest.patterns import HeadingPatterns
from app.ingest.reference_inventory import (
    reference_row_binding_issues,
    reference_source_scope_issues,
)
from app.ingest.references import (
    ReferenceEntryLocation,
    extract_references,
    non_reference_blocks,
    possible_split_doi,
    reference_entry_locations,
    reference_reading_order_issues,
    reference_section_page,
    select_references,
)
from app.ingest.schemas import ExtractionResult
from app.llm.base import LLMClient
from app.models.citation import Citation
from app.models.enums import CheckKind, CitationParseStatus, FlagSeverity, ResultOutcome
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckResult, CheckRun, Flag


class VerdictKind(StrEnum):
    unassessed = "unassessed"
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
    metadata_mismatch = "metadata_mismatch"


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
    metadata_comparison: MetadataComparison | None = None
    unassessed_reason: str | None = None


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
    "The attempted registry or catalogue lookup did not return this source. "
    "Possible local/unindexed source, a typo in "
    "the citation, or a source these providers don't cover, please check "
    "manually. Unverifiable, not necessarily incorrect."
)
UNVERIFIABLE_API_DOWN_WORDING = (
    "The lookup for this source could not be completed. "
    "This does not show that the source is missing. "
    "Review the reference details or check the source manually."
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
    session: AsyncSession,
    client: httpx.AsyncClient,
    citation: Citation,
    *,
    settings: Settings,
    instructor_id: int | None = None,
    title_policy: TitlePolicy | None = None,
) -> CitationVerdict:
    """Existence: DOI → CrossRef; books (ISBN) → Open Library→Google Books;
    else (no DOI/ISBN) → metadata search CrossRef→Semantic Scholar (ticket
    responsibilities). A DOI that fails to resolve is reported unverifiable
    directly, NOT retried via title search — falling back could match a
    fabricated/mismatched citation to an unrelated real paper by title
    alone, which would be a false reassurance (worse than a false flag,
    charter judgment #1)."""
    if citation.doi and possible_split_doi(citation.raw_text, citation.doi):
        # Also protects old stored prefixes and direct callers: no existence,
        # retraction or supplementary-abstract request can verify a partial key.
        return CitationVerdict(VerdictKind.unassessed)
    if citation.doi:
        try:
            title_policy = title_policy or load_title_policy(
                settings.citation_metadata_title_policy_file
            )
        except (OSError, ValueError, UnicodeError):
            return CitationVerdict(
                VerdictKind.unassessed, unassessed_reason="metadata_comparison_policy_unavailable"
            )
        verdict = await _verify_keyed(
            session,
            "doi",
            citation.doi,
            settings=settings,
            fetch=lambda: _lookup_doi_chain(client, citation.doi, settings),
            required_providers=settings.citation_doi_providers,
            instructor_id=instructor_id,
            reference_text=citation.raw_text,
        )
        if verdict.result and verdict.result.found:
            if not same_doi(returned_doi(verdict.result), citation.doi):
                return CitationVerdict(VerdictKind.unassessed, verdict.result)
            if verdict.kind in (VerdictKind.retracted, VerdictKind.corrected):
                return verdict
            comparison = compare_metadata(
                citation, verdict.result, policy=title_policy, settings=settings
            )
            verdict = replace(verdict, metadata_comparison=comparison)
            if comparison.differing_fields:
                return replace(verdict, kind=VerdictKind.metadata_mismatch)
            if comparison.title_status == "unassessed":
                return replace(
                    verdict,
                    kind=VerdictKind.unassessed,
                    unassessed_reason=comparison.unassessed_reason,
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
            and verdict.result.content_checkable
            and not verdict.result.abstract
        ):
            abstract = await _fetch_supplementary_abstract(
                session, client, citation.doi, settings=settings
            )
            if abstract:
                verdict = replace(verdict, result=replace(verdict.result, abstract=abstract))
        return verdict

    if citation.isbn:

        async def _book_lookup() -> VerificationResult:
            result = await openlibrary_client.lookup_isbn(client, citation.isbn, settings=settings)
            if not result.found and citation.title:
                result = await gbooks_client.search_title(client, citation.title, settings=settings)
            return result

        verdict = await _verify_keyed(
            session,
            "isbn",
            citation.isbn,
            settings=settings,
            fetch=_book_lookup,
            instructor_id=instructor_id,
            reference_text=citation.raw_text,
        )
        if (
            verdict.result is not None
            and verdict.result.found
            and verdict.result.provider == "gbooks"
        ):
            raw = verdict.result.raw
            info = raw.get("volumeInfo", {}) if isinstance(raw, dict) else {}
            identifiers = info.get("industryIdentifiers", []) if isinstance(info, dict) else []
            expected = re.sub(r"[\s-]", "", citation.isbn).upper()
            bound = (
                bool(expected)
                and isinstance(identifiers, list)
                and any(
                    isinstance(item, dict)
                    and item.get("type") in {"ISBN_10", "ISBN_13"}
                    and isinstance(item.get("identifier"), str)
                    and re.sub(r"[\s-]", "", item["identifier"]).upper() == expected
                    for item in identifiers
                )
            )
            if not bound:
                return replace(
                    verdict,
                    kind=VerdictKind.unassessed,
                    unassessed_reason="book_search_identity_unconfirmed",
                )
        return verdict

    if citation.title:

        async def _title_search() -> VerificationResult:
            result = await crossref_client.search_by_title(
                client, citation.title, settings=settings
            )
            if not result.found:
                result = await s2_client.search_by_title(client, citation.title, settings=settings)
            return result

        try:
            title_policy = title_policy or load_title_policy(
                settings.citation_metadata_title_policy_file
            )
        except (OSError, ValueError, UnicodeError):
            return CitationVerdict(
                VerdictKind.unassessed, unassessed_reason="metadata_comparison_policy_unavailable"
            )
        verdict = await _verify_keyed(
            session,
            "title",
            citation.title.casefold(),
            settings=settings,
            fetch=_title_search,
            instructor_id=instructor_id,
            reference_text=citation.raw_text,
        )
        if verdict.result is not None and verdict.result.found:
            comparison = compare_metadata(
                citation, verdict.result, policy=title_policy, settings=settings
            )
            # Search returns candidates, including cached candidates. An unrelated
            # first hit cannot confirm this reference or transfer a retraction.
            bound = (
                comparison.title_status == "agrees"
                and not comparison.differing_fields
                and comparison.author_status in {"agrees", "source_supported_sequence"}
                and type(citation.year) is int
                and type(comparison.registered.get("year")) is int
                and comparison.registered["year"] == citation.year
            )
            if not bound:
                return replace(
                    verdict,
                    kind=VerdictKind.unassessed,
                    metadata_comparison=comparison,
                    unassessed_reason="title_search_identity_unconfirmed",
                )
            return replace(verdict, metadata_comparison=comparison)
        return verdict

    # No DOI/ISBN/title to key a lookup on (e.g. a parse_failed entry) —
    # genuinely unverifiable, no network call made.
    return CitationVerdict(VerdictKind.not_found)


async def _lookup_doi_chain(client, doi, settings):
    attempted, unavailable = [], False
    lookups = {"crossref": crossref_client.lookup_doi, "datacite": datacite_client.lookup_doi}
    for provider in settings.citation_doi_providers:
        if provider not in lookups:
            raise ApiDownError("The configured DOI provider is unsupported.")
        attempted.append(provider)
        try:
            result = await lookups[provider](client, doi, settings=settings)
        except (ApiDownError, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            unavailable = True
            continue
        if result.found:
            return replace(result, attempted_providers=attempted)
    if unavailable:
        raise ApiDownError("At least one DOI registry could not be checked.")
    return VerificationResult(
        found=False,
        provider=attempted[-1] if attempted else "unassessed",
        attempted_providers=attempted,
    )


async def _fetch_supplementary_abstract(
    session: AsyncSession, client: httpx.AsyncClient, doi: str, *, settings: Settings
) -> str | None:
    cached = await cache.get_cached(
        session,
        key_kind="doi_abstract",
        key_value=doi,
        stale_days=settings.citation_cache_stale_days,
    )
    if cached is not None and (not cached.found or same_doi(returned_doi(cached), doi)):
        return cached.abstract
    try:
        result = await s2_client.lookup_doi(client, doi, settings=settings)
    except (ApiDownError, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return None
    await cache.store_result(
        session, key_kind="doi_abstract", key_value=doi, provider="s2", result=result
    )
    return result.abstract if same_doi(returned_doi(result), doi) else None


async def _verify_keyed(
    session,
    key_kind,
    key_value,
    *,
    settings,
    fetch,
    required_providers=(),
    instructor_id=None,
    reference_text=None,
) -> CitationVerdict:
    cached = await cache.get_cached(
        session,
        key_kind=key_kind,
        key_value=key_value,
        stale_days=settings.citation_cache_stale_days,
    )
    if (
        cached is not None
        and not cached.found
        and not set(required_providers).issubset(cached.attempted_providers)
    ):
        cached = None
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
        session,
        key_kind=key_kind,
        key_value=key_value,
        instructor_id=instructor_id,
        reference_text=reference_text,
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
    if verdict.kind in (
        VerdictKind.existence_confirmed,
        VerdictKind.instructor_confirmed,
        VerdictKind.unassessed,
    ):
        # Unassessed identifiers are disclosed through coverage, never as
        # "not found"; confirmed sources do not need an issue flag.
        return None
    anchor = f"reference #{citation.order_index + 1}"
    if verdict.kind == VerdictKind.metadata_mismatch:
        return CitationFlagDraft(
            severity=FlagSeverity.low,
            evidence_excerpt=citation.raw_text,
            page_anchor=anchor,
            detail={
                "kind": "possible_metadata_mismatch",
                "reason": (
                    "The DOI identifies a registered source, but its published metadata differs "
                    "from this reference. Compare the author, title and year before relying on it. "
                    "Source existence does not establish claim support."
                ),
                "provider": verdict.result.provider,
                "differing_fields": list(verdict.metadata_comparison.differing_fields)
                if verdict.metadata_comparison
                else [],
                "metadata_comparison": verdict.metadata_comparison.evidence()
                if verdict.metadata_comparison
                else None,
            },
        )
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
            detail={
                "kind": "unverifiable_api_down",
                "reason": UNVERIFIABLE_API_DOWN_WORDING,
                "affects_readiness": False,
            },
        )
    detail = {
        "kind": "unverifiable_not_found",
        "reason": UNVERIFIABLE_NOT_FOUND_WORDING,
        "providers_attempted": verdict.result.attempted_providers if verdict.result else [],
    }
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


def _with_reference_anchor(
    draft: CitationFlagDraft,
    order_index: int | None,
    locations: dict[tuple[int, str], ReferenceEntryLocation],
) -> CitationFlagDraft:
    """Navigate to a source entry only when both its order and exact excerpt agree."""
    if type(order_index) is not int or order_index < 0:
        return draft
    location = locations.get((order_index + 1, draft.evidence_excerpt))
    if location is None:
        return draft
    anchor = (
        f"p. {location.page}"
        if location.page is not None
        else f"\u00b6{location.paragraph}"
        if location.paragraph is not None
        else draft.page_anchor
    )
    return replace(draft, page_anchor=anchor)


async def _citation_recency(
    session: AsyncSession,
    check_run_id: int,
    citations: list[Citation],
    extraction: ExtractionResult,
    settings: Settings,
    blocked_reason: str | None,
) -> RecencyAssessment:
    """The recency note (V-092) under the format's setting for this run.

    Runs only on a reference list whose entries are bound to the source; a
    list that could not be established is reported as unassessed rather
    than read for dates."""
    stored = await session.scalar(
        select(Rubric.citation_recency)
        .join(CheckRun, CheckRun.rubric_id == Rubric.id)
        .where(CheckRun.id == check_run_id)
    )
    try:
        policy = resolve_policy(stored, settings)
    except ValueError:
        return RecencyAssessment(unassessed_summary("format_setting_invalid"))
    if not policy.enabled:
        return RecencyAssessment(off_summary(policy))
    if blocked_reason:
        return RecencyAssessment(unassessed_summary(blocked_reason, policy))
    try:
        grammar = load_grammar(settings.citation_recency_grammar_file)
        created_at = await session.scalar(
            select(CheckRun.created_at).where(CheckRun.id == check_run_id)
        )
        check_year = check_year_of(created_at or datetime.now(UTC), settings)
    except (OSError, ValueError, UnicodeError, ZoneInfoNotFoundError):
        return RecencyAssessment(unassessed_summary("recency_policy_unavailable", policy))
    return assess_recency(citations, extraction, policy, grammar, check_year)


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
    native_source = analytical_source(extraction)
    source_limit = source_limitation_detail(extraction)
    selection = select_references(extraction, patterns)
    membership_partial = bool(selection.membership_issues)
    inventory_issues: list[str] = []
    # An empty inventory keeps its existing no-reference treatment. A nonempty
    # assessment must bind the whole retained list before inferring absence or
    # spending metadata/model calls, including ordinary unquoted references.
    if citations:
        inventory_issues.extend(selection.issues)
        inventory_issues.extend(reference_row_binding_issues(selection.groups, citations))
        inventory_issues.extend(
            reference_source_scope_issues(extraction, selection, require_complete=False)
        )
    source_projections, canonical_drafts = {}, None
    projection_reason = "reference_source_projection_unbound" if inventory_issues else None
    if not projection_reason:
        citations, source_projections = project_quoted_metadata(
            citations, extraction, patterns, settings=settings
        )
        projection_reason = next(
            (row["reason"] for row in source_projections.values() if row.get("reason")), None
        )
        if not projection_reason and citations:
            canonical_drafts = extract_references(extraction, patterns)
            if len(canonical_drafts) != len(citations):
                inventory_issues.append("reference_metadata_inventory_unavailable")
            elif any(
                getattr(draft, field) != getattr(citation, field)
                for draft, citation in zip(canonical_drafts, citations, strict=True)
                for field in SOURCE_FIELDS
                if field != "raw_text"  # already compared under canonical source normalization
            ):
                inventory_issues.append("reference_metadata_fields_mismatch")
            if inventory_issues:
                projection_reason = "reference_source_projection_unbound"
    inventory_detail = {
        "reference_membership_status": "partial"
        if membership_partial
        else "not_established"
        if projection_reason or not citations
        else "complete",
        "reference_membership_issues": list(selection.membership_issues),
        "reference_inventory_status": "bound"
        if citations and not projection_reason
        else "not_captured",
        "reference_inventory_issues": list(dict.fromkeys(inventory_issues)),
        "n_source_reference_groups": len(selection.groups),
        "n_retained_reference_rows": len(citations),
    }
    if projection_reason:
        detail = {
            **native_source.detail(),
            **inventory_detail,
            "analysis_coverage": "unsupported",
            "coverage_reason": projection_reason,
            "n_references": len(citations),
            "n_metadata_skipped_insufficient": len(citations),
            "n_linkage_unassessed": len(citations),
            "n_claim_support_checked": 0,
            "claim_support_inventory_status": "not_captured",
            "citation_recency": (
                await _citation_recency(
                    session,
                    check_run_id,
                    citations,
                    extraction,
                    settings,
                    "reference_list_not_bound" if citations else "no_references",
                )
            ).summary,
            "n_flags": 0,
            "reference_metadata_coverage": [
                {
                    "reference_order_index": c.order_index,
                    "status": "unassessed",
                    "reason": projection_reason,
                    "lookup_attempted": False,
                    "metadata_complete": False,
                    "source_projection": source_projections.get(c.order_index),
                }
                for c in citations
            ],
        }
        if source_limit is not None:
            return await persist_source_limitation(
                session,
                check_run_id,
                CheckKind.citation_integrity,
                extraction,
                supplemental_detail=detail,
            )
        result = CheckResult(
            check_run_id=check_run_id,
            criterion_id=None,
            kind=CheckKind.citation_integrity,
            outcome=ResultOutcome.escalated,
            detail=detail,
        )
        session.add(result)
        await write_audit_event(
            session,
            event_type="citation_integrity_check_computed",
            check_run_id=check_run_id,
            payload={**detail, "outcome": result.outcome.value},
        )
        await session.commit()
        return result
    metadata_only = False
    if source_limit is not None:
        # Metadata verification has a bounded source: the complete bibliography.
        # Admit it only when both source boundaries/provenance and the persisted
        # entries agree. No body fragments are joined across unsupported pages.
        drafts = (
            canonical_drafts
            if canonical_drafts is not None
            else extract_references(extraction, patterns)
        )
        metadata_only = bool(
            drafts
            and len(drafts) == len(citations)
            and not selection.issues
            and not reference_reading_order_issues(extraction, selection)
            and all(
                all(getattr(draft, field) == getattr(citation, field) for field in SOURCE_FIELDS)
                for draft, citation in zip(drafts, citations, strict=True)
            )
        )
        if not metadata_only:
            recency = await _citation_recency(
                session, check_run_id, citations, extraction, settings, "source_unavailable"
            )
            return await persist_source_limitation(
                session,
                check_run_id,
                CheckKind.citation_integrity,
                extraction,
                supplemental_detail={"citation_recency": recency.summary},
            )
    body_blocks = (
        []
        if metadata_only or membership_partial or projection_reason
        else non_reference_blocks(extraction, patterns)
    )
    body_blocks = [block for scope in native_source.scopes_for(body_blocks) for block in scope]
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
    identity_unassessed, identifier_leads = set(), []
    if not numbered:
        absence_candidates, identity_unassessed, identifier_leads = guarded_absence_candidates(
            absence_candidates, citations, body_blocks, settings
        )
    # Text left unread (native Word equations, pages with unresolved order)
    # may hold the only citation of a reference, so absence stays unassessed.
    partial_source = native_source.limited or bool(extraction.unread_pages)
    native_absence_unassessed = (
        {ref.order_index for ref in absence_candidates} if partial_source else set()
    )
    # No supported body style or a partial scan cannot prove a reference unused.
    if (
        metadata_only
        or membership_partial
        or partial_source
        or projection_reason
        or gaps
        or selection.issues
        or (not numbered and not cross.linked)
    ):
        absence_candidates = []
    unassessed_indexes = (
        {ref.order_index for ref in numeric.absence_unassessed_references}
        if numbered
        else identity_unassessed
        | {
            ref.order_index
            for ref in (cross.possibly_cited + (cross.uncited if not cross.linked else []))
        }
    ) | native_absence_unassessed
    if metadata_only or membership_partial or projection_reason or gaps or selection.issues:
        unassessed_indexes = {ref.order_index for ref in citations}
    linkage_unassessed = len(unassessed_indexes)
    metadata_unassessed = set()
    comparison_unassessed = False
    metadata_api_down = 0
    metadata_not_found = 0
    verification_records = []
    usable_metadata = all(
        c.parse_status == CitationParseStatus.parsed
        and c.authors
        and reference_date_key(c) is not None
        for c in citations
    )

    # Metadata belongs to the reference; support belongs to each captured use.
    # Occurrence indexes are local inventory positions, not canonical source IDs.
    claim_occurrences: list[dict] = []
    occurrences_by_reference: dict[int, list[int]] = {}
    content_unassessed: set[int] = set()

    def capture_occurrences(mark, matched_refs, *, numeric_mark: bool) -> None:
        cropped = mark.excerpt_cropped if numeric_mark else mark.claim_excerpt_cropped
        reason = (
            "ambiguous_linkage"
            if len(matched_refs) != 1
            else "cropped_claim"
            if cropped
            else "weak_numeric_context"
            if numeric_mark and not mark.strong_citation_context
            else "claim_unavailable"
            if not mark.claim_sentence
            else None
        )
        for ref in matched_refs:
            index = len(claim_occurrences)
            claim_occurrences.append(
                {
                    "occurrence_index": index,
                    "reference_order_index": ref.order_index,
                    "source_anchor": mark.anchor,
                    "page": mark.page,
                    "paragraph": mark.paragraph,
                    "raw_mark": mark.raw_text,
                    "claim_sentence": mark.claim_sentence,
                    "source_block_index": mark.block_index if numeric_mark else None,
                    "source_offset": mark.start if numeric_mark else None,
                    "eligible": False,
                    "eligibility_reason": reason,
                    "eligible_pair_index": None,
                    "status": "unassessed",
                    "unassessed_reason": reason or "metadata_unavailable",
                }
            )
            occurrences_by_reference.setdefault(ref.order_index, []).append(index)
            if reason:
                content_unassessed.add(ref.order_index)

    for mark, matched_refs in cross.linked:
        capture_occurrences(mark, matched_refs, numeric_mark=False)
    for mark, matched_refs in numeric.linked:
        capture_occurrences(mark, matched_refs, numeric_mark=True)

    flag_drafts: list[CitationFlagDraft] = (
        numeric_linkage_flags(numeric.orphan_marks, absence_candidates, len(citations))
        if numbered
        else [
            *(orphan_flags(cross.orphans) if usable_metadata else []),
            *uncited_flags(absence_candidates),
        ]
    )
    if (
        metadata_only
        or membership_partial
        or projection_reason
        or selection.issues
        or not citations
    ):
        flag_drafts = []
    claim_support_pairs: list[ClaimSupportInput] = []
    candidate_by_pair_index: list[int] = []
    locations = {
        (location.index, location.raw_text): location
        for location in reference_entry_locations(extraction, patterns)
    }
    references_by_index = {citation.order_index: citation for citation in citations}
    flag_drafts = [
        _with_reference_anchor(
            with_reference_locator(draft, references_by_index.get(draft.detail.get("order_index"))),
            draft.detail.get("order_index"),
            locations,
        )
        if draft.detail.get("kind") == "uncited_reference"
        else draft
        for draft in flag_drafts
    ]
    instructor_id = await session.scalar(
        select(Manuscript.instructor_id)
        .join(CheckRun, CheckRun.manuscript_id == Manuscript.id)
        .where(CheckRun.id == check_run_id)
    )
    title_policy, policy_unavailable = None, False
    if any(c.doi for c in citations):
        try:
            title_policy = load_title_policy(settings.citation_metadata_title_policy_file)
        except (OSError, ValueError, UnicodeError):
            policy_unavailable = True
    for citation in citations:
        parsed = citation.parse_status == CitationParseStatus.parsed
        identifier_unassessed = bool(
            citation.doi and possible_split_doi(citation.raw_text, citation.doi)
        )
        metadata_complete = bool(
            parsed
            and citation.title
            and citation.authors
            and isinstance(citation.year, int)
            and not identifier_unassessed
        )
        lookup_eligible = (
            parsed
            and not identifier_unassessed
            and (bool(citation.doi or citation.isbn) or metadata_complete)
        )
        if not metadata_complete:
            metadata_unassessed.add(citation.order_index)
        if not lookup_eligible:
            for index in occurrences_by_reference.get(citation.order_index, []):
                occurrence = claim_occurrences[index]
                if occurrence["eligibility_reason"] is None:
                    occurrence["eligibility_reason"] = "metadata_incomplete"
                    occurrence["unassessed_reason"] = "metadata_incomplete"
                    content_unassessed.add(citation.order_index)
            verification_records.append(
                {
                    "reference_order_index": citation.order_index,
                    "status": "unassessed",
                    "reason": (
                        source_projections[citation.order_index]["reason"]
                        if citation.order_index in source_projections
                        and source_projections[citation.order_index].get("reason")
                        else "ambiguous_identifier_wrap"
                        if identifier_unassessed
                        else "unsupported_reference_structure"
                    ),
                    "lookup_attempted": False,
                    "metadata_complete": False,
                    **(
                        {"source_projection": source_projections[citation.order_index]}
                        if citation.order_index in source_projections
                        else {}
                    ),
                }
            )
            continue
        verdict = (
            CitationVerdict(
                VerdictKind.unassessed, unassessed_reason="metadata_comparison_policy_unavailable"
            )
            if citation.doi and policy_unavailable
            else await verify_citation(
                session,
                client,
                citation,
                settings=settings,
                instructor_id=instructor_id,
                title_policy=title_policy,
            )
        )
        if verdict.kind == VerdictKind.unassessed:
            metadata_unassessed.add(citation.order_index)
        if verdict.metadata_comparison and verdict.metadata_comparison.title_status == "unassessed":
            comparison_unassessed = True
            metadata_unassessed.add(citation.order_index)
        metadata_api_down += verdict.kind == VerdictKind.api_down
        metadata_not_found += verdict.kind == VerdictKind.not_found
        verification_records.append(
            {
                "reference_order_index": citation.order_index,
                "status": verdict.kind.value,
                "lookup_attempted": verdict.unassessed_reason
                != "metadata_comparison_policy_unavailable",
                "reason": verdict.unassessed_reason,
                "metadata_comparison": verdict.metadata_comparison.evidence()
                if verdict.metadata_comparison
                else None,
                "lookup_kind": "doi" if citation.doi else "isbn" if citation.isbn else "title",
                "metadata_complete": metadata_complete,
                **(
                    {"source_projection": source_projections[citation.order_index]}
                    if citation.order_index in source_projections
                    else {}
                ),
                "provider": verdict.result.provider if verdict.result else None,
                "providers_attempted": verdict.result.attempted_providers if verdict.result else [],
                "retraction_status": "checked"
                if verdict.result and verdict.result.provider == "crossref"
                else "unassessed",
            }
        )
        draft = _verdict_flag_draft(citation, verdict)
        if draft is not None:
            draft = with_reference_locator(draft, citation)
            if citation.order_index in source_projections:
                draft = replace(
                    draft,
                    detail={
                        **draft.detail,
                        "source_projection": source_projections[citation.order_index],
                    },
                )
            flag_drafts.append(_with_reference_anchor(draft, citation.order_index, locations))
        for index in occurrences_by_reference.get(citation.order_index, []):
            occurrence = claim_occurrences[index]
            if occurrence["eligibility_reason"] is not None:
                continue
            reason = (
                "metadata_incomplete"
                if not metadata_complete
                else "metadata_unavailable"
                if verdict.kind != VerdictKind.existence_confirmed or verdict.result is None
                else "abstract_unavailable"
                if not verdict.result.abstract
                else None
            )
            if reason:
                occurrence["eligibility_reason"] = reason
                occurrence["unassessed_reason"] = reason
                content_unassessed.add(citation.order_index)
                continue
            occurrence["eligible"] = True
            occurrence["eligible_pair_index"] = len(claim_support_pairs)
            occurrence["unassessed_reason"] = "no_llm"
            candidate_by_pair_index.append(index)
            claim_support_pairs.append(
                ClaimSupportInput(
                    citation=citation,
                    claim_sentence=occurrence["claim_sentence"],
                    abstract=verdict.result.abstract,
                    claim_anchor=occurrence["source_anchor"],
                )
            )

    claim_support_result = ClaimSupportResult([], 0, 0, 0)
    if llm is not None and claim_support_pairs:
        claim_support_result = await run_claim_support_check(
            llm, claim_support_pairs, check_run_id=check_run_id, settings=settings
        )
        flag_drafts.extend(claim_support_result.flags)
        records, duplicate_indexes = {}, set()
        for record in claim_support_result.pair_results:
            if type(record.pair_index) is not int or not 0 <= record.pair_index < len(
                candidate_by_pair_index
            ):
                continue
            if record.pair_index in records:
                duplicate_indexes.add(record.pair_index)
            records[record.pair_index] = record
        for pair_index, candidate_index in enumerate(candidate_by_pair_index):
            occurrence = claim_occurrences[candidate_index]
            record = records.get(pair_index)
            if record is None or pair_index in duplicate_indexes:
                # Internal result mappings must not invent successful coverage.
                occurrence["unassessed_reason"] = "parse_failure"
                continue
            occurrence.update(
                {key: value for key, value in asdict(record).items() if key != "pair_index"}
            )
            if record.unassessed_reason == "limit":
                content_unassessed.add(occurrence["reference_order_index"])
    elif claim_support_pairs:
        content_unassessed.update(pair.citation.order_index for pair in claim_support_pairs)

    claim_support_checked = sum(item["status"] == "assessed" for item in claim_occurrences)
    skipped_support = {
        reason: sum(
            item["eligible"] and item["unassessed_reason"] == reason for item in claim_occurrences
        )
        for reason in (
            "limit",
            "quota",
            "api_down",
            "parse_failure",
            "ai_not_configured",
            "ai_key_rejected",
        )
    }

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
        projection_reason
        if projection_reason
        else "source_reading_order_partial"
        if metadata_only
        else "reference_membership_partial"
        if membership_partial
        else "reference_list_unavailable"
        if selection.issues or not citations
        else "native_math_citation_incomplete"
        if native_source.limited
        else "metadata_comparison_policy_unavailable"
        if policy_unavailable
        else "citation_metadata_comparison_unassessed"
        if comparison_unassessed
        else "reference_search_identity_unconfirmed"
        if any(
            record.get("reason")
            in {"title_search_identity_unconfirmed", "book_search_identity_unconfirmed"}
            for record in verification_records
        )
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
    elif (
        metadata_not_found
        or skipped_support["parse_failure"] > 0
        or skipped_support["ai_not_configured"] > 0
        or skipped_support["ai_key_rejected"] > 0
    ):
        outcome = ResultOutcome.unverifiable
    elif metadata_api_down or skipped_support["api_down"] > 0:
        outcome = ResultOutcome.api_down
    elif skipped_support["quota"] > 0:
        outcome = ResultOutcome.quota_exhausted
    else:
        outcome = ResultOutcome.passed

    # Recency notes are low-severity observations about the reference list
    # itself; they never change the check's coverage outcome above.
    recency = await _citation_recency(
        session,
        check_run_id,
        citations,
        extraction,
        settings,
        "no_references"
        if not citations
        else "reference_membership_partial"
        if membership_partial
        else "reference_list_unavailable"
        if selection.issues
        else None,
    )
    for draft in recency.flags:
        citation = references_by_index.get(draft.detail["order_index"])
        flag_drafts.append(
            _with_reference_anchor(
                with_reference_locator(draft, citation), citation.order_index, locations
            )
        )

    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=None,
        kind=CheckKind.citation_integrity,
        outcome=outcome,
        detail={
            **native_source.detail(),
            **inventory_detail,
            **(
                {
                    "assessment_scope": "reference_metadata_only",
                    "analysis_coverage": "partial"
                    if any(record["lookup_attempted"] for record in verification_records)
                    else "unsupported",
                    "n_protected_source_fragments": len(selection.protected_span),
                }
                if membership_partial
                else {}
            ),
            **(
                {
                    "analysis_coverage": "partial"
                    if body_blocks
                    or any(record["lookup_attempted"] for record in verification_records)
                    else "unsupported",
                    "assessment_scope": "available_text_and_reference_metadata",
                    "n_reference_metadata_attempted": sum(
                        record["lookup_attempted"] for record in verification_records
                    ),
                }
                if native_source.limited
                else {}
            ),
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
            "n_reference_metadata_attempted": sum(
                record["lookup_attempted"] for record in verification_records
            ),
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
            "identifier_linkage_leads": identifier_leads,
            "reference_metadata_coverage": verification_records,
            "search_identity_policy_version": "title-author-year-isbn-v1",
            "reference_list_page": reference_section_page(extraction, patterns),
            "reference_selection_issues": list(selection.issues),
            "citation_parse_issues": sorted(gaps),
            "n_claim_support_checked": claim_support_checked,
            **(
                {"claim_support_inventory_status": "not_captured"}
                if metadata_only or membership_partial or not citations
                else {
                    "n_claim_support_pairs_total": len(claim_support_pairs),
                    "n_claim_support_candidate_pairs_total": len(claim_occurrences),
                    "n_claim_support_pairs_unassessed": len(claim_occurrences)
                    - claim_support_checked,
                    "claim_support_occurrences": claim_occurrences,
                }
            ),
            "n_claim_support_skipped_limit": skipped_support["limit"],
            "n_claim_support_skipped_quota": skipped_support["quota"],
            "n_claim_support_skipped_api_down": skipped_support["api_down"],
            "n_claim_support_skipped_parse_failure": skipped_support["parse_failure"],
            "n_claim_support_skipped_ai_not_configured": skipped_support["ai_not_configured"],
            "n_claim_support_skipped_ai_key_rejected": skipped_support["ai_key_rejected"],
            "citation_recency": recency.summary,
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
            "thresholds": {
                "cache_stale_days": settings.citation_cache_stale_days,
                "claim_support_max_pairs_per_call": settings.claim_support_max_pairs_per_call,
                "claim_support_max_pairs_per_run": settings.claim_support_max_pairs_per_run,
            },
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
