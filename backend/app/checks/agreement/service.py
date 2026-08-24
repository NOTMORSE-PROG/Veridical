"""Internal Agreement Check (F4) assembly: V-034's extraction + V-035's
pairing, into one `check_result` + real `Flag` rows — same shape as
`app.checks.forensics.service.run_statistical_forensics_check` (F6's
assembly), applied to F4.

**Applicability gate**: a manuscript with no intent AND no outcome
statements at all gets `outcome=not_applicable` — N/A is not "passed"
(charter rule 9), never a silently-clean check_run when there was nothing
to check. A manuscript with statements that all got fully judged gets
`outcome=passed` (whether or not it also carries flags — matches F5/F6's
own convention). **BUG-073**: a run that skipped even one candidate pair
(quota/API/parse-failure — `pair.py`'s `PairingResult`) does NOT get
`passed` — see this module's own outcome-priority comment below.
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

    # BUG-073: a check that skipped even one candidate pair did NOT fully
    # execute -- `passed` is reserved for a run that judged everything it
    # set out to. Same priority as F5's citation-integrity check
    # (app/checks/citations/verify.py) when causes mix within one run:
    # `unverifiable` (a parse failure, D-017's defect class, must never
    # hide behind a more benign-sounding cause) first, then `api_down`,
    # then `quota_exhausted` last. Real per-cause counts always live in
    # `detail` regardless of which one outcome wins.
    if pairing.n_skipped_parse_failure > 0:
        outcome = ResultOutcome.unverifiable
    elif pairing.n_skipped_api_down > 0:
        outcome = ResultOutcome.api_down
    elif pairing.n_skipped_quota > 0:
        outcome = ResultOutcome.quota_exhausted
    else:
        outcome = ResultOutcome.passed

    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=None,
        kind=CheckKind.internal_agreement,
        outcome=outcome,
        detail={
            "n_intents": len(intents),
            "n_outcomes": len(outcomes),
            "n_flags": len(pairing.flags),
            "n_unmatched_outcomes": pairing.n_unmatched_outcomes,
            "n_skipped_quota": pairing.n_skipped_quota,
            "n_skipped_api_down": pairing.n_skipped_api_down,
            "n_skipped_parse_failure": pairing.n_skipped_parse_failure,
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
