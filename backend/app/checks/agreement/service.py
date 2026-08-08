"""Internal Agreement Check (F4) assembly: V-034's extraction + V-035's
pairing, into one `check_result` + real `Flag` rows — same shape as
`app.checks.forensics.service.run_statistical_forensics_check` (F6's
assembly), applied to F4.

**Applicability gate**: a manuscript with no intent AND no outcome
statements at all gets `outcome=not_applicable` — N/A is not "passed"
(charter rule 9), never a silently-clean check_run when there was nothing
to check. Any manuscript with at least one statement gets `outcome=passed`
(whether or not it also carries flags — matches F5/F6's own convention).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.agreement.extract import extract_statements
from app.checks.agreement.pair import run_agreement_pairing
from app.config import Settings
from app.ingest.schemas import ExtractionResult
from app.llm.base import LLMClient
from app.models.enums import CheckKind, ResultOutcome
from app.models.run import CheckResult, Flag


async def run_internal_agreement_check(
    session: AsyncSession,
    llm: LLMClient,
    check_run_id: int,
    extraction: ExtractionResult,
    settings: Settings,
) -> CheckResult:
    outcome_extraction = extract_statements(extraction, settings=settings)
    intents, outcomes = outcome_extraction.intents, outcome_extraction.outcomes

    if not intents and not outcomes:
        result = CheckResult(
            check_run_id=check_run_id,
            criterion_id=None,
            kind=CheckKind.internal_agreement,
            outcome=ResultOutcome.not_applicable,
            detail={
                "n_intents": 0,
                "n_outcomes": 0,
                "n_flags": 0,
                "note": "No objective/finding-style statements were detected in this manuscript.",
            },
        )
        session.add(result)
        await session.commit()
        return result

    pairing = await run_agreement_pairing(
        llm, intents, outcomes, check_run_id=check_run_id, settings=settings
    )

    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=None,
        kind=CheckKind.internal_agreement,
        outcome=ResultOutcome.passed,
        detail={
            "n_intents": len(intents),
            "n_outcomes": len(outcomes),
            "n_flags": len(pairing.flags),
            "n_unmatched_outcomes": pairing.n_unmatched_outcomes,
            "n_skipped_for_quota": pairing.n_skipped_for_quota,
        },
    )
    session.add(result)
    await session.flush()  # need result.id before attaching flags
    for draft in pairing.flags:
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


async def existing_internal_agreement_result(
    session: AsyncSession, check_run_id: int
) -> CheckResult | None:
    """Idempotency guard (same contract every other stage keeps, ENGINEERING
    §4) — a resumed run must not re-run agreement checks it already did."""
    return await session.scalar(
        select(CheckResult).where(
            CheckResult.check_run_id == check_run_id,
            CheckResult.kind == CheckKind.internal_agreement,
        )
    )
