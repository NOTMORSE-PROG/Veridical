"""Statistical forensics (F6) assembly: V-031's extraction + V-032's GRIM/
GRIMMER + V-033's p-recalculation and sanity checks, into one
`check_result` + real `Flag` rows — same shape as
`app.checks.citations.verify.run_citation_integrity_check` (F5's
assembly), applied to F6.

**Applicability gate (F6.5)**: a manuscript with no statistics at all
(purely qualitative — the ticket's own example) gets
`outcome=not_applicable` — N/A is not "passed" (charter rule 9), never a
silently-clean check_run when there was nothing to check. Any manuscript
with EITHER inferential or descriptive stats gets `outcome=passed`
(whether or not it also carries flags — matches F5's own convention:
`outcome` answers "did the check run meaningfully", `Flag` rows are the
actual findings).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.forensics.checks import ForensicsFlagDraft, evaluate_grim_grimmer
from app.checks.forensics.extract import extract_all_stats
from app.checks.forensics.pcheck import evaluate_p_recalc
from app.checks.forensics.sanity import evaluate_group_counts, evaluate_percentage_sums
from app.ingest.schemas import ExtractionResult
from app.models.enums import CheckKind, ResultOutcome
from app.models.run import CheckResult, Flag


async def run_statistical_forensics_check(
    session: AsyncSession, check_run_id: int, extraction: ExtractionResult
) -> CheckResult:
    stats = extract_all_stats(extraction)
    inferential = [s for s in stats if s.kind == "inferential"]
    descriptive = [s for s in stats if s.kind == "descriptive"]

    outcome = ResultOutcome.passed if (inferential or descriptive) else ResultOutcome.not_applicable

    full_text = "\n".join(b.text for b in extraction.blocks if not b.is_furniture)

    flag_drafts: list[ForensicsFlagDraft] = [
        *evaluate_grim_grimmer(stats),
        *evaluate_p_recalc(inferential, full_text),
        *evaluate_percentage_sums(stats),
        *evaluate_group_counts(stats),
    ]

    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=None,
        kind=CheckKind.statistical_forensics,
        outcome=outcome,
        detail={
            "n_inferential_stats": len(inferential),
            "n_descriptive_stats": len(descriptive),
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
    await session.commit()
    return result


async def existing_statistical_forensics_result(
    session: AsyncSession, check_run_id: int
) -> CheckResult | None:
    """Idempotency guard (same contract every other stage keeps, ENGINEERING
    §4) — a resumed run must not re-run forensics checks it already did."""
    return await session.scalar(
        select(CheckResult).where(
            CheckResult.check_run_id == check_run_id,
            CheckResult.kind == CheckKind.statistical_forensics,
        )
    )
