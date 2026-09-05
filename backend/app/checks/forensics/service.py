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

import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_event
from app.checks.forensics.checks import ForensicsFlagDraft, evaluate_grim_grimmer
from app.checks.forensics.extract import extract_all_stats
from app.checks.forensics.pcheck import evaluate_p_recalc
from app.checks.forensics.sanity import evaluate_group_counts, evaluate_percentage_sums
from app.ingest.schemas import ExtractionResult
from app.models.enums import CheckKind, ResultOutcome
from app.models.run import CheckResult, Flag


@dataclass(frozen=True)
class _ForensicsComputation:
    n_inferential: int
    n_descriptive: int
    outcome: ResultOutcome
    flag_drafts: list[ForensicsFlagDraft]
    skipped_composite_rows: int


def _compute_forensics_findings(extraction: ExtractionResult) -> _ForensicsComputation:
    """BUG-152: the CPU-bound half of the check (regex stat extraction,
    GRIM/GRIMMER, p-value recalculation, sanity checks over the whole
    manuscript text) has no I/O and no DB access, so it's pulled out into
    one plain function callable off the event loop in one executor
    round-trip -- matching the codebase's own established convention
    (`ingest/service.py`, `report/service.py:manuscript_file_path_for`,
    `ingest/service.py::load_raw_store_async`) rather than blocking the
    single Render worker (and the 2s progress poll reporting on this exact
    work) for the run's duration."""
    stats = extract_all_stats(extraction)
    inferential = [s for s in stats if s.kind == "inferential"]
    descriptive = [s for s in stats if s.kind == "descriptive"]

    outcome = ResultOutcome.passed if (inferential or descriptive) else ResultOutcome.not_applicable

    full_text = "\n".join(b.text for b in extraction.blocks if not b.is_furniture)

    grim_grimmer = evaluate_grim_grimmer(stats)
    flag_drafts: list[ForensicsFlagDraft] = [
        *grim_grimmer.flags,
        *evaluate_p_recalc(inferential, full_text),
        *evaluate_percentage_sums(stats),
        *evaluate_group_counts(stats),
    ]
    return _ForensicsComputation(
        n_inferential=len(inferential),
        n_descriptive=len(descriptive),
        outcome=outcome,
        flag_drafts=flag_drafts,
        skipped_composite_rows=grim_grimmer.skipped_composite_rows,
    )


async def run_statistical_forensics_check(
    session: AsyncSession, check_run_id: int, extraction: ExtractionResult
) -> CheckResult:
    computation = await asyncio.get_running_loop().run_in_executor(
        None, _compute_forensics_findings, extraction
    )
    outcome = computation.outcome
    flag_drafts = computation.flag_drafts

    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=None,
        kind=CheckKind.statistical_forensics,
        outcome=outcome,
        detail={
            "n_inferential_stats": computation.n_inferential,
            "n_descriptive_stats": computation.n_descriptive,
            "n_flags": len(flag_drafts),
            # BUG-164: disclosed, never silently omitted -- rows GRIM/
            # GRIMMER deliberately never evaluated because they look like
            # a multi-item composite mean (the same n repeats across
            # other rows of the same table), not the single-item mean
            # the test's own math assumes.
            "n_grim_skipped_likely_composite": computation.skipped_composite_rows,
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
    # BUG-151: F6 makes zero LLM calls, so it wrote nothing to the audit
    # log at all -- see `checks/reuse/service.py`'s sibling call for the
    # same fix applied to F7 (charter judgment 4: traceable verdicts).
    await write_audit_event(
        session,
        event_type="statistical_forensics_check_computed",
        check_run_id=check_run_id,
        payload={**result.detail, "outcome": result.outcome.value},
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
