"""Internal Agreement Check (F4) assembly: V-034's extraction + V-035's
pairing, into one `check_result` + real `Flag` rows — same shape as
`app.checks.forensics.service.run_statistical_forensics_check` (F6's
assembly), applied to F4.

**Recognition coverage**: zero extracted candidates leaves applicability
unassessed. Lexical recognition cannot establish that objectives/results
are absent, so it cannot support missing-statement conclusions.
Extracted roles are not yet independently established as this
study's own objectives/results, so comparisons remain escalated review
leads, never readiness deductions. Quota/API/parse failures remain distinct
outcomes with their actual skip counts.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_event
from app.checks.agreement.extract import extract_statements
from app.checks.agreement.pair import PROMPT_VERSION, run_agreement_pairing
from app.checks.source_quality import persist_source_limitation
from app.config import Settings
from app.ingest.analytical_source import analytical_source
from app.ingest.schemas import ExtractionResult
from app.llm.base import LLMClient
from app.models.enums import CheckKind, ResultOutcome
from app.models.run import CheckResult, Flag


def _agreement_thresholds(settings: Settings) -> dict[str, float | int]:
    """BUG-151 (backend review finding): both env-configurable thresholds
    that decide this check's own behavior -- `dedup` narrows candidate
    intent/outcome statements before pairing (`extract.py`), `pairing_floor`
    excludes a candidate pair from ever reaching the LLM (`pair.py`) -- so a
    past verdict is reconstructable after either changes, the same guarantee
    F7's own audit event already gives `reuse_*_threshold`. The number of
    matches judged per objective changes the findings as much, so it is
    recorded too."""
    return {
        "dedup_similarity_threshold": settings.agreement_dedup_similarity_threshold,
        "pairing_similarity_floor": settings.agreement_pairing_similarity_floor,
        "pairing_max_candidates_per_intent": settings.agreement_pairing_max_candidates_per_intent,
        "context_sentences_before": settings.agreement_context_sentences_before,
        "context_sentences_after": settings.agreement_context_sentences_after,
        "context_max_chars": settings.agreement_context_max_chars,
        "scope_statements_per_pair": settings.agreement_scope_statements_per_pair,
        "scope_statement_max_chars": settings.agreement_scope_statement_max_chars,
        "contradiction_confirmations": settings.agreement_contradiction_confirmations,
        "contradiction_majority_of_three": settings.agreement_contradiction_majority_of_three,
        "quote_min_chars": settings.agreement_quote_min_chars,
        "context_locate_chars": settings.agreement_context_locate_chars,
        "scope_min_word_chars": settings.agreement_scope_min_word_chars,
        "min_result_words": settings.agreement_min_result_words,
    }


async def run_internal_agreement_check(
    session: AsyncSession,
    llm: LLMClient,
    check_run_id: int,
    extraction: ExtractionResult,
    settings: Settings,
) -> CheckResult:
    source_stop = await persist_source_limitation(
        session, check_run_id, CheckKind.internal_agreement, extraction
    )
    if source_stop is not None:
        return source_stop
    source = analytical_source(extraction)
    outcome_extraction = extract_statements(extraction, settings=settings)
    intents, outcomes = outcome_extraction.intents, outcome_extraction.outcomes

    if not intents and not outcomes:
        result = CheckResult(
            check_run_id=check_run_id,
            criterion_id=None,
            kind=CheckKind.internal_agreement,
            outcome=ResultOutcome.escalated,
            detail={
                **source.detail(),
                "coverage_reason": "native_math_source_incomplete"
                if source.limited
                else "statement_extraction_unassessed",
                "n_intents": 0,
                "n_outcomes": 0,
                "n_flags": 0,
                "note": (
                    "No objective/finding-style statements were detected in the available source. "
                    "Native Word source coverage is incomplete; this does not establish absence."
                    if source.limited
                    else "The detector did not identify objective/result candidates to compare. "
                    "This does not establish that objectives or results are absent. "
                    "Review the manuscript directly."
                ),
            },
        )
        session.add(result)
        # BUG-151: a check-level summary event, distinct from the per-call
        # `llm_call` rows F4's own pairing calls already write (V-009) --
        # neither this early "nothing to check" branch nor the main path
        # below had ANY audit trail of the check's own overall verdict
        # before this fix. See `checks/reuse/service.py`'s sibling call
        # for the same fix applied to F7 (charter judgment 4).
        await write_audit_event(
            session,
            event_type="internal_agreement_check_computed",
            check_run_id=check_run_id,
            payload={
                **result.detail,
                "outcome": result.outcome.value,
                "thresholds": _agreement_thresholds(settings),
            },
        )
        await session.commit()
        return result

    pairing = await run_agreement_pairing(
        llm,
        intents,
        outcomes,
        check_run_id=check_run_id,
        settings=settings,
        allow_absence_leads=False,
        extraction=extraction,
    )

    # BUG-073: a check that skipped even one candidate pair did NOT fully
    # execute -- `passed` is reserved for a run that judged everything it
    # set out to. Same priority as F5's citation-integrity check
    # (app/checks/citations/verify.py) when causes mix within one run:
    # `unverifiable` (a parse failure, D-017's defect class, must never
    # hide behind a more benign-sounding cause) first, then `api_down`,
    # then `quota_exhausted` last. Real per-cause counts always live in
    # `detail` regardless of which one outcome wins.
    if (
        pairing.n_skipped_parse_failure > 0
        or pairing.n_skipped_ai_not_configured > 0
        or pairing.n_skipped_ai_key_rejected > 0
    ):
        outcome = ResultOutcome.unverifiable
    elif pairing.n_skipped_api_down > 0:
        outcome = ResultOutcome.api_down
    elif pairing.n_skipped_quota > 0:
        outcome = ResultOutcome.quota_exhausted
    else:
        outcome = ResultOutcome.escalated

    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=None,
        kind=CheckKind.internal_agreement,
        outcome=outcome,
        detail={
            **source.detail(),
            "absence_leads_withheld": True,
            "n_intents": len(intents),
            "coverage_reason": "statement_roles_unverified",
            "reason": (
                "Statement roles have not been verified as this study's own "
                "objectives and results. "
                "The retained comparisons are review leads, not readiness deductions."
            ),
            "n_outcomes": len(outcomes),
            "n_flags": len(pairing.flags),
            "n_unmatched_outcomes": pairing.n_unmatched_outcomes,
            "n_skipped_quota": pairing.n_skipped_quota,
            "n_skipped_api_down": pairing.n_skipped_api_down,
            "n_skipped_parse_failure": pairing.n_skipped_parse_failure,
            "n_skipped_ai_not_configured": pairing.n_skipped_ai_not_configured,
            "n_skipped_ai_key_rejected": pairing.n_skipped_ai_key_rejected,
            "prompt_version": PROMPT_VERSION,
            # Pairs the judge could not decide are coverage, shown once on the
            # report beside the findings, not one finding row each.
            "undetermined": {
                "count": len(pairing.undetermined),
                "compared_count": pairing.n_compared,
                "pairs": pairing.undetermined,
            },
            "verdict_counts": pairing.verdict_counts,
            "contradictions_withheld": pairing.downgrades,
            "explained_in_context": pairing.explained_in_context,
            # Results set aside as not the thesis's own (literature review,
            # needs survey, ...), or the note that no results section could be
            # identified and every section was used.
            "results_sections": pairing.results_sections,
            # Objectives set aside the same way (BUG-625).
            "objective_sections": pairing.objective_sections,
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
    await write_audit_event(
        session,
        event_type="internal_agreement_check_computed",
        check_run_id=check_run_id,
        payload={
            **result.detail,
            "outcome": result.outcome.value,
            "thresholds": _agreement_thresholds(settings),
        },
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
