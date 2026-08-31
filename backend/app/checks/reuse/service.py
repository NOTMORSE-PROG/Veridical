"""Originality/reuse check (F7) assembly: V-036's embedding pipeline +
V-037's similarity query, into one `check_result` + real `Flag` rows —
same shape as `app.checks.forensics.service.run_statistical_forensics_check`.

**Write-back happens AFTER the check completes and the flags are
persisted** (ticket AC, F7.3) — `query_similar_manuscripts` runs first
against whatever is already in the archive, and only once that result is
committed does this manuscript's own embedding get written in, so a
re-run of the SAME check_run (idempotency, ENGINEERING §4) never
compares this manuscript against itself.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.reuse.embed import compute_document_embeddings, compute_passage_embeddings
from app.checks.reuse.query import (
    PassageMatch,
    SimilarityMatch,
    is_first_upload_for_instructor,
    query_similar_manuscripts,
    query_similar_passages,
)
from app.checks.reuse.store import store_document_embeddings, store_passage_embeddings
from app.config import Settings
from app.ingest.schemas import ExtractionResult
from app.models.enums import CheckKind, FlagSeverity, ResultOutcome
from app.models.run import CheckResult, Flag

# BUG-050 Branch B (owner, 2026-08-16) / BUG-097: the F7 corpus is shared
# across instructor accounts BY DESIGN — that decision is not what these
# wordings are about. What they must never do is turn a match into a
# content leak: `newcomer` reproduced a day-one account's very first
# report exposing another instructor's real group name AND the matched
# document's actual chapter heading text. A match is reported via an
# opaque, non-identifying reference (`{ref}`, the archived manuscript's
# internal id) — enough to make the match IDENTIFIABLE (BUG-050 item 5;
# can be referred back to, e.g. once V-066's browse screen exists) without
# making it IDENTIFYING (no other account's real label or heading ever
# appears in text an instructor reads). `matched_group_label` and
# `matched_chapter_title` stay in `detail` below for internal use only —
# confirmed never serialized to any API response (`app/flags/service.py`
# reads only `verdict`/`basis` off `detail`).
EXACT_DUPLICATE_WHOLE_DOC_WORDING = (
    "This manuscript appears to be an exact or near-exact textual duplicate of "
    "archived manuscript #{ref} in VERIDICAL's shared originality library: "
    "possible resubmission or reuse. Please verify manually."
)
HIGH_SIMILARITY_WHOLE_DOC_WORDING = (
    "This manuscript shows high textual similarity to archived manuscript "
    "#{ref} in VERIDICAL's shared originality library: possible shared content "
    "or reuse. Please verify manually."
)
EXACT_DUPLICATE_CHAPTER_WORDING = (
    'The section "{own_chapter}" appears to be an exact or near-exact textual '
    "duplicate of a section in archived manuscript #{ref} in VERIDICAL's "
    "shared originality library: possible resubmission or reuse of that section. "
    "Please verify manually."
)
HIGH_SIMILARITY_CHAPTER_WORDING = (
    'The section "{own_chapter}" shows high textual similarity to a section '
    "in archived manuscript #{ref} in VERIDICAL's shared originality library: "
    "possible reuse of that section. Please verify manually."
)

# F7.4 (V-072) passage-level wording. Deliberately different SHAPE from the
# templates above, not just different text: those go into `Flag.reason`
# AND `Flag.evidence_excerpt` (there is no real quotable text to excerpt at
# whole-doc/chapter granularity, so the sentence stands in for both). A
# passage match DOES have real quotable text -- the passage itself -- so
# `evidence_excerpt` becomes the passage's own words (bounded to
# `reuse_passage_chunk_words`) and these templates go into
# `Flag.detail["reason"]` only, which `app.flags.service._to_flag_out`
# already surfaces as `ai_reasoning`. This also means passage flags are the
# ONLY F7 flags whose `evidence_excerpt` is real page text, not a
# templated sentence -- which is what lets V-065's `page.search_for()`-based
# region recovery actually find and highlight them precisely (measured
# 13/13 for real quoted prose, `app/ingest/regions.py`'s own module
# docstring), unlike today's whole-doc/chapter flags, which only ever
# resolve to a coarse `section`/`whole_document` region.
EXACT_DUPLICATE_PASSAGE_WORDING = (
    "This passage appears to be an exact or near-exact textual duplicate of a "
    "passage in archived manuscript #{ref} in VERIDICAL's shared originality "
    "library: possible resubmission or reuse of this passage. Please verify manually."
)
HIGH_SIMILARITY_PASSAGE_WORDING = (
    "This passage shows high textual similarity to a passage in archived "
    "manuscript #{ref} in VERIDICAL's shared originality library: possible reuse "
    "of this passage. Please verify manually."
)

# BUG-097 (presentation-only remedy, owner ruling 2026-08-24): drives
# `detail["first_upload_context"]` below, which surfaces as the report's
# distinct `FirstUploadContextBanner`/`FirstUploadContextGroupNote`
# (frontend) — never changes severity or scoring (see
# `query.is_first_upload_for_instructor`'s own docstring for why a
# severity change was rejected).
#
# `ux-critic` finding (2026-08-24, live review of the built banner): an
# earlier version of this fix ALSO appended a caveat sentence to `reason`
# (surfaced via `FlagOut.ai_reasoning`) — with the banner rendering right
# above it, the same "no track record yet, verify carefully" idea was
# stated twice in near-identical wording on one screen, competing for
# attention (Nielsen #4, minimalist design) and undermining the banner's
# own job of being the one clear, distinct signal. The banner is the
# sole disclosure surface now; `reason`/`ai_reasoning` stay exactly what
# the underlying match template already says.


def _match_to_flag_draft(
    match: SimilarityMatch, *, first_upload: bool = False
) -> tuple[FlagSeverity, str, dict]:
    is_chapter = match.matched_chapter_title is not None
    if match.level == "exact_duplicate":
        severity = FlagSeverity.high
        template = (
            EXACT_DUPLICATE_CHAPTER_WORDING if is_chapter else EXACT_DUPLICATE_WHOLE_DOC_WORDING
        )
    else:
        severity = FlagSeverity.med
        template = (
            HIGH_SIMILARITY_CHAPTER_WORDING if is_chapter else HIGH_SIMILARITY_WHOLE_DOC_WORDING
        )

    reason = template.format(
        ref=match.matched_manuscript_id,
        own_chapter=match.own_chapter_title,
    )
    detail = {
        "kind": f"reuse_{match.level}{'_chapter' if is_chapter else ''}",
        "reason": reason,
        "similarity": round(match.similarity, 3),
        "matched_manuscript_id": match.matched_manuscript_id,
        "matched_group_label": match.matched_group_label,
        "matched_chapter_title": match.matched_chapter_title,
        "own_chapter_title": match.own_chapter_title,
        "first_upload_context": first_upload,
    }
    return severity, reason, detail


def _passage_match_to_flag_draft(
    match: PassageMatch, *, first_upload: bool = False
) -> tuple[FlagSeverity, str, str, dict]:
    """Returns (severity, evidence_excerpt, page_anchor, detail) — a
    different shape from `_match_to_flag_draft` above (see the wording
    templates' own comment for why: real passage text as the excerpt,
    the explanatory sentence in `detail["reason"]` instead)."""
    if match.level == "exact_duplicate":
        severity = FlagSeverity.high
        template = EXACT_DUPLICATE_PASSAGE_WORDING
    else:
        severity = FlagSeverity.med
        template = HIGH_SIMILARITY_PASSAGE_WORDING
    reason = template.format(ref=match.matched_manuscript_id)

    page_anchor = (
        f"p. {match.own_page}" if match.own_page is not None else f"¶{match.own_paragraph}"
    )
    detail = {
        "kind": f"reuse_{match.level}_passage",
        "reason": reason,
        "first_upload_context": first_upload,
        "similarity": round(match.similarity, 3),
        "matched_manuscript_id": match.matched_manuscript_id,
        "matched_group_label": match.matched_group_label,  # internal only, BUG-050/097
        "own_chapter_index": match.own_chapter_index,
        "own_context_text": match.own_context_text,
        "matched_chapter_index": match.matched_chapter_index,
        "matched_page": match.matched_page,
        "matched_paragraph": match.matched_paragraph,
        "matched_text": match.matched_text,
        "matched_context_text": match.matched_context_text,
        "is_reference_list_match": match.own_is_reference_list or match.matched_is_reference_list,
        "is_block_quote_match": match.own_is_block_quote or match.matched_is_block_quote,
    }
    return severity, match.own_text, page_anchor, detail


async def run_originality_reuse_check(
    session: AsyncSession,
    manuscript_id: int,
    check_run_id: int,
    extraction: ExtractionResult,
    settings: Settings,
) -> CheckResult:
    embeddings = compute_document_embeddings(extraction, settings)

    if embeddings.whole_document is None:
        result = CheckResult(
            check_run_id=check_run_id,
            criterion_id=None,
            kind=CheckKind.originality_reuse,
            outcome=ResultOutcome.not_applicable,
            detail={
                "archive_size_n": 0,
                "n_flags": 0,
                "note": "No embeddable content was extracted from this manuscript.",
            },
        )
        session.add(result)
        await session.commit()
        return result

    query_result = await query_similar_manuscripts(session, manuscript_id, embeddings, settings)
    # F7.4 (V-072): passage-level candidates alongside the whole-doc/chapter
    # ones above -- a genuinely different chunker (`compute_passage_embeddings`),
    # queried separately (`query_similar_passages` excludes reference-list/
    # block-quote passages by default, ticket AC3), but folded into the
    # SAME check_result (one F7 result per run, more flags at finer
    # granularity — same "flags at every matching level" precedent the
    # existing exact-duplicate test already documents for chapter vs
    # whole-doc).
    passages = compute_passage_embeddings(extraction, settings)
    passage_query_result = await query_similar_passages(session, manuscript_id, passages, settings)
    # BUG-097 (presentation-only remedy): computed once, applied to every
    # match this run produces — see `is_first_upload_for_instructor`'s own
    # docstring for why this changes wording/context only, never severity.
    first_upload = await is_first_upload_for_instructor(session, manuscript_id)

    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=None,
        kind=CheckKind.originality_reuse,
        outcome=ResultOutcome.passed,
        detail={
            "archive_size_n": query_result.archive_size_n,
            "passage_archive_size_n": passage_query_result.passage_archive_size_n,
            "n_flags": len(query_result.matches) + len(passage_query_result.matches),
            "first_upload_context": first_upload,
        },
    )
    session.add(result)
    await session.flush()  # need result.id before attaching flags
    for match in query_result.matches:
        severity, reason, detail = _match_to_flag_draft(match, first_upload=first_upload)
        anchor = match.own_chapter_title or "whole document"
        session.add(
            Flag(
                check_result_id=result.id,
                severity=severity,
                evidence_excerpt=reason,
                page_anchor=anchor,
                detail=detail,
            )
        )
    for passage_match in passage_query_result.matches:
        severity, evidence_excerpt, page_anchor, detail = _passage_match_to_flag_draft(
            passage_match, first_upload=first_upload
        )
        session.add(
            Flag(
                check_result_id=result.id,
                severity=severity,
                evidence_excerpt=evidence_excerpt,
                page_anchor=page_anchor,
                detail=detail,
            )
        )
    await session.commit()

    # Write-back AFTER the check + its flags are committed (ticket AC,
    # F7.3) — never match against self. Passage write-back follows the
    # same ordering for the same reason.
    await store_document_embeddings(session, manuscript_id, embeddings)
    if passages:
        await store_passage_embeddings(session, manuscript_id, passages, settings)

    return result


async def existing_originality_reuse_result(
    session: AsyncSession, check_run_id: int
) -> CheckResult | None:
    """Idempotency guard (ENGINEERING §4) — a resumed run must not
    re-query-and-reflag (or double-write-back) work it already did."""
    return await session.scalar(
        select(CheckResult).where(
            CheckResult.check_run_id == check_run_id,
            CheckResult.kind == CheckKind.originality_reuse,
        )
    )
