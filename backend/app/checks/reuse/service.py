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

from app.checks.reuse.embed import compute_document_embeddings
from app.checks.reuse.query import SimilarityMatch, query_similar_manuscripts
from app.checks.reuse.store import store_document_embeddings
from app.config import Settings
from app.ingest.schemas import ExtractionResult
from app.models.enums import CheckKind, FlagSeverity, ResultOutcome
from app.models.run import CheckResult, Flag

EXACT_DUPLICATE_WHOLE_DOC_WORDING = (
    "This manuscript appears to be a duplicate or near-duplicate ({pct}% match) of "
    'a manuscript previously processed by VERIDICAL ("{other_group}") — possible '
    "resubmission or reuse. Please verify manually."
)
HIGH_SIMILARITY_WHOLE_DOC_WORDING = (
    "This manuscript shows high similarity ({pct}% match) to a manuscript "
    'previously processed by VERIDICAL ("{other_group}") — possible shared content '
    "or reuse. Please verify manually."
)
EXACT_DUPLICATE_CHAPTER_WORDING = (
    'The section "{own_chapter}" appears to be a duplicate or near-duplicate '
    '({pct}% match) of "{other_chapter}" in a manuscript previously processed by '
    'VERIDICAL ("{other_group}") — possible resubmission or reuse of that section. '
    "Please verify manually."
)
HIGH_SIMILARITY_CHAPTER_WORDING = (
    'The section "{own_chapter}" shows high similarity ({pct}% match) to '
    '"{other_chapter}" in a manuscript previously processed by VERIDICAL '
    '("{other_group}") — possible reuse of that section. Please verify manually.'
)


def _match_to_flag_draft(match: SimilarityMatch) -> tuple[FlagSeverity, str, dict]:
    pct = round(match.similarity * 100, 1)
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
        pct=pct,
        other_group=match.matched_group_label,
        own_chapter=match.own_chapter_title,
        other_chapter=match.matched_chapter_title,
    )
    detail = {
        "kind": f"reuse_{match.level}{'_chapter' if is_chapter else ''}",
        "reason": reason,
        "similarity": round(match.similarity, 3),
        "matched_manuscript_id": match.matched_manuscript_id,
        "matched_group_label": match.matched_group_label,
        "matched_chapter_title": match.matched_chapter_title,
        "own_chapter_title": match.own_chapter_title,
    }
    return severity, reason, detail


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

    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=None,
        kind=CheckKind.originality_reuse,
        outcome=ResultOutcome.passed,
        detail={
            "archive_size_n": query_result.archive_size_n,
            "n_flags": len(query_result.matches),
        },
    )
    session.add(result)
    await session.flush()  # need result.id before attaching flags
    for match in query_result.matches:
        severity, reason, detail = _match_to_flag_draft(match)
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
    await session.commit()

    # Write-back AFTER the check + its flags are committed (ticket AC,
    # F7.3) — never match against self.
    await store_document_embeddings(session, manuscript_id, embeddings)

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
