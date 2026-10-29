"""Flag evidence/annotation/override (F8.2/F8.4, screen 4i).

Mirrors `app.checks.escalation`'s resolution SHAPE by design (reason
mandatory, audit-logged, triggers a live rescore) — not shared code,
because they operate on different tables for a reason documented in
STATE.md's 2026-07-25 design-call entry: an escalation is uncertainty in
OUR grading process; a flag is "a possible inconsistency" in the
manuscript itself (charter rule 3) — conflating the two data models
would blur that wording precision. Overriding a flag never touches its
parent `check_result.outcome/score` — only the flag's own
`overridden`/`override_reason`, which `app.report.scoring._flag_deduction`
already excludes from the severity deduction once set (V-019, unchanged
by this ticket).
"""

from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.citations.history_review import citation_metadata_review_warning
from app.checks.citations.source_locator import citation_source_locator
from app.checks.reuse.embed import split_context
from app.config import get_settings
from app.errors import ConflictError, NotFoundError
from app.external import cache
from app.findings.policy import exclusion_reason, reuse_excerpt, reuse_reasoning
from app.flags.schemas import (
    AgreementPairOut,
    CitationMetadataComparisonOut,
    CitationSourceKeyOut,
    ConfirmCitationSourceOut,
    ConfirmFindingOut,
    FlagOut,
    OverrideFlagOut,
    PassagePairOut,
    PValueComparisonOut,
)
from app.models.audit import AuditLog
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion
from app.models.run import CheckResult, CheckRun, Flag
from app.report.assessment import assessment_write, lock_assessment
from app.report.cited_work import cited_work
from app.report.scoring import flag_ai_verdict_summary
from app.report.service import aggregate_and_score, get_report


def _passage_pair_from_detail(evidence_excerpt: str, detail: dict) -> PassagePairOut | None:
    kind = detail.get("kind") or ""
    if not isinstance(kind, str):
        return None
    if kind.endswith("_passage"):
        return _stored_passage_pair(
            own_excerpt=evidence_excerpt,
            own_context=detail.get("own_context_text", ""),
            matched_excerpt=detail.get("matched_text", ""),
            matched_context=detail.get("matched_context_text", ""),
            matched_ref=detail.get("matched_manuscript_id"),
            similarity=detail.get("similarity", 0.0),
            level="exact_duplicate" if "exact_duplicate" in kind else "high_similarity",
        )
    # BUG-153: a whole-document/chapter-level match (`kind` has no
    # "_passage" suffix) carries its own SUPPORTING passage under a
    # distinct detail key, set by `checks.reuse.service`'s flag-drafting
    # loop -- `evidence_excerpt` here is the templated accusation sentence
    # (see that module's wording templates' own docstring), never real
    # manuscript text, so it can't double as `own_excerpt` the way it does
    # for a genuine passage-level flag above.
    supporting = detail.get("supporting_passage")
    if not isinstance(supporting, dict):
        return None
    return _stored_passage_pair(
        own_excerpt=supporting.get("own_text", ""),
        own_context=supporting.get("own_context_text", ""),
        matched_excerpt=supporting.get("matched_text", ""),
        matched_context=supporting.get("matched_context_text", ""),
        matched_ref=supporting.get("matched_manuscript_id"),
        similarity=supporting.get("similarity", 0.0),
        level=supporting.get("level"),
    )


def _stored_passage_pair(
    *, own_excerpt, own_context, matched_excerpt, matched_context, matched_ref, similarity, level
) -> PassagePairOut | None:
    """A malformed historical pair is unavailable, never an export-wide failure."""
    if type(matched_ref) is not int or matched_ref <= 0:
        return None
    if not all(
        isinstance(value, str) and value.strip() for value in (own_excerpt, matched_excerpt)
    ):
        return None
    own_before, own_after = split_context(
        own_context if isinstance(own_context, str) else "", own_excerpt
    )
    matched_before, matched_after = split_context(
        matched_context if isinstance(matched_context, str) else "", matched_excerpt
    )
    try:
        return PassagePairOut(
            own_excerpt=own_excerpt,
            own_context_before=own_before,
            own_context_after=own_after,
            matched_ref=matched_ref,
            matched_excerpt=matched_excerpt,
            matched_context_before=matched_before,
            matched_context_after=matched_after,
            context_words_each_side=get_settings().reuse_passage_context_words,
            similarity=similarity,
            level=level,
        )
    except ValidationError:
        return None


async def _scoped_flag(
    session: AsyncSession, flag_id: int, instructor_id: int
) -> tuple[Flag, CheckResult, Manuscript, CheckRun]:
    row = (
        await session.execute(
            select(Flag, CheckResult, Manuscript, CheckRun)
            .join(CheckResult, CheckResult.id == Flag.check_result_id)
            .join(CheckRun, CheckRun.id == CheckResult.check_run_id)
            .join(Manuscript, Manuscript.id == CheckRun.manuscript_id)
            .where(Flag.id == flag_id, Manuscript.instructor_id == instructor_id)
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        raise NotFoundError(f"No flag {flag_id}.")
    return row


def _citation_source_key(detail: dict) -> CitationSourceKeyOut | None:
    """BUG-078: `verify.py` sets `key_kind`/`key_value` on a
    `unverifiable_not_found` flag's detail only when a real DOI/ISBN/title
    existed to key the lookup on (never for the "no identifier at all"
    case) -- presence here is exactly what gates the "Confirm this source"
    button on screen 4i (design review spec, 2026-08-24)."""
    if detail.get("kind") != "unverifiable_not_found":
        return None
    key_kind = detail.get("key_kind")
    key_value = detail.get("key_value")
    if not key_kind or not key_value:
        return None
    return CitationSourceKeyOut(kind=key_kind, value=key_value)


def _distinct_reasoning(detail: dict, evidence_excerpt: str) -> str | None:
    """`ai_reasoning`'s fallback source (`detail["reason"]`) is, for some
    checks, the same sentence already set as `evidence_excerpt` -- BUG-112.
    Suppress it in that case so the frontend never renders one sentence
    twice in a row; `detail["reasoning"]` (a genuinely distinct field, set
    by semantic grading) is never suppressed."""
    reasoning = detail.get("reasoning") or detail.get("reason")
    if reasoning is not None and reasoning == evidence_excerpt:
        return None
    return reasoning


def _p_value_comparison(check_kind: str, detail: dict) -> PValueComparisonOut | None:
    if check_kind != "statistical_forensics" or detail.get("kind") not in (
        "p_value_mismatch",
        "p_value_decision_error",
    ):
        return None
    reported, computed = detail.get("reported_p_display"), detail.get("computed_p_display")
    if not all(isinstance(value, str) and value.strip() for value in (reported, computed)):
        return None
    return PValueComparisonOut(reported=reported, recalculated=computed)


def _agreement_pair(check_kind: str, detail: dict) -> AgreementPairOut | None:
    if check_kind != "internal_agreement" or detail.get("kind") not in (
        "agreement_contradictory",
        "agreement_partial",
        "agreement_cannot_determine",
        "agreement_injection_suspected",
    ):
        return None
    intent, outcome = detail.get("intent_text"), detail.get("outcome_text")
    if not all(isinstance(value, str) and value.strip() for value in (intent, outcome)):
        return None
    return AgreementPairOut(
        intent_text=intent,
        intent_anchor=detail.get("intent_anchor")
        if isinstance(detail.get("intent_anchor"), str)
        else None,
        outcome_text=outcome,
        outcome_anchor=detail.get("outcome_anchor")
        if isinstance(detail.get("outcome_anchor"), str)
        else None,
    )


def _citation_metadata_comparison(
    check_kind: str, detail: dict
) -> CitationMetadataComparisonOut | None:
    if check_kind != "citation_integrity" or detail.get("kind") != "possible_metadata_mismatch":
        return None
    try:
        return CitationMetadataComparisonOut.model_validate(detail.get("metadata_comparison"))
    except ValidationError:
        return None


async def _to_flag_out(
    session: AsyncSession,
    flag: Flag,
    result: CheckResult,
    manuscript: Manuscript,
    check_run: CheckRun,
    *,
    assessment_revision: int | None = None,
) -> FlagOut:
    criterion_text = None
    if result.criterion_id is not None:
        criterion = await session.get(Criterion, result.criterion_id)
        criterion_text = criterion.text if criterion else None
    # Per-flag detail (V-033) takes priority — F5/F6 checks put many flags
    # under one check_result, each needing its own reason; falls back to
    # check_result.detail for the older one-flag-per-check_result shape
    # (semantic grading, V-020) where the reason still lives there.
    detail = flag.detail or result.detail or {}
    passage_pair = _passage_pair_from_detail(flag.evidence_excerpt, detail)
    kind = detail.get("kind")
    has_stored_pair = (
        isinstance(kind, str) and kind.endswith("_passage")
    ) or "supporting_passage" in detail
    return FlagOut(
        id=flag.id,
        assessment_revision=assessment_revision,
        check_result_id=result.id,
        check_run_id=result.check_run_id,
        manuscript_group_label=manuscript.group_label,
        check_kind=result.kind.value,
        criterion_text=criterion_text,
        severity=flag.severity.value,
        confidence=float(flag.confidence) if flag.confidence is not None else None,
        evidence_excerpt=(
            reuse_excerpt(detail, flag.evidence_excerpt)
            if result.kind.value == "originality_reuse"
            else flag.evidence_excerpt
        ),
        page_anchor=flag.page_anchor,
        annotation=flag.annotation,
        overridden=flag.overridden,
        affects_readiness=exclusion_reason(result.kind.value, detail) is None,
        readiness_exclusion_reason=exclusion_reason(result.kind.value, detail),
        override_reason=flag.override_reason,
        # BUG-053: was `detail.get("verdict") or detail.get("basis")` only
        # -- those two keys are semantic grading's own vocabulary (V-020),
        # and F4/F5/F6/F7 flags never set them (they use "kind"/"reason"
        # instead), so every non-semantic flag rendered "AI verdict:
        # unavailable" regardless of whether a real finding existed.
        # `flag_ai_verdict_summary` is the single source both this label
        # and the scoring engine's own "does this flag count" gate share.
        ai_verdict_summary=flag_ai_verdict_summary(detail),
        # BUG-112: some checks (F7 reuse) set detail["reason"] to the exact
        # same sentence already shown as evidence_excerpt -- falling back to
        # it here would render that sentence twice on screen 4i. A general
        # guard, not a check-specific patch, so any future check that makes
        # the same convenience choice doesn't reintroduce the duplication.
        ai_reasoning=(
            reuse_reasoning(detail, flag.evidence_excerpt)
            if result.kind.value == "originality_reuse"
            else _distinct_reasoning(detail, flag.evidence_excerpt)
        ),
        p_value_comparison=_p_value_comparison(result.kind.value, detail),
        agreement_pair=_agreement_pair(result.kind.value, detail),
        citation_metadata_comparison=_citation_metadata_comparison(result.kind.value, detail),
        citation_source_locator=citation_source_locator(
            result.kind.value, flag.evidence_excerpt, flag.detail
        ),
        citation_metadata_review_warning=citation_metadata_review_warning(
            result.kind.value, flag.evidence_excerpt, detail
        ),
        llm_mode=check_run.llm_mode.value,
        # BUG-170: same expression as `report/service.py`'s `FlagSummaryOut`
        # field of the same name -- `flag.detail` directly (not the
        # `detail` merge above), since F7 reuse checks always populate
        # `flag.detail["kind"]` themselves (V-033 per-flag detail).
        is_passage_level=bool((flag.detail or {}).get("kind", "").endswith("_passage")),
        passage_pair=passage_pair,
        matched_ref=detail.get("matched_manuscript_id"),
        first_upload_context=bool(detail.get("first_upload_context")),
        evidence_unavailable=bool(detail.get("evidence_unavailable"))
        or (result.kind.value == "originality_reuse" and has_stored_pair and passage_pair is None),
        citation_source_key=_citation_source_key(detail),
        confirmed_citation_source=flag.confirmed_citation_source,
        finding_confirmed=bool(flag.finding_confirmed),
        finding_confirm_note=flag.finding_confirm_note,
        finding_confirmed_at=flag.finding_confirmed_at,
        **cited_work(flag.detail or {}, flag.evidence_excerpt),
    )


async def get_flag(session: AsyncSession, flag_id: int, instructor_id: int) -> FlagOut:
    flag, result, manuscript, check_run = await _scoped_flag(session, flag_id, instructor_id)
    report = await lock_assessment(session, result.check_run_id)
    flag, result, manuscript, check_run = await _scoped_flag(session, flag_id, instructor_id)
    return await _to_flag_out(
        session,
        flag,
        result,
        manuscript,
        check_run,
        assessment_revision=report.assessment_revision if report is not None else None,
    )


async def annotate_flag(
    session: AsyncSession, flag_id: int, instructor_id: int, annotation: str
) -> FlagOut:
    flag, result, manuscript, check_run = await _scoped_flag(session, flag_id, instructor_id)
    flag.annotation = annotation.strip()
    session.add(
        AuditLog(
            event_type="flag_annotated",
            check_run_id=result.check_run_id,
            payload={
                "flag_id": flag_id,
                "check_result_id": result.id,
                "instructor_id": instructor_id,
                "annotation": annotation.strip(),
            },
        )
    )
    await session.commit()
    return await get_flag(session, flag_id, instructor_id)


async def override_flag(
    session: AsyncSession,
    flag_id: int,
    instructor_id: int,
    reason: str,
    *,
    expected_revision: int,
) -> OverrideFlagOut:
    """Override the owned flag and bind its rescore acknowledgment atomically."""
    _flag, initial_result, _manuscript, _run = await _scoped_flag(session, flag_id, instructor_id)
    run_id = initial_result.check_run_id
    async with assessment_write(session, run_id, instructor_id, expected_revision) as report:
        flag, result, manuscript, check_run = await _scoped_flag(session, flag_id, instructor_id)
        flag.overridden = True
        flag.override_reason = reason.strip()
        # Dismissing replaces an earlier confirmation; the audit keeps both.
        flag.finding_confirmed = False
        flag.finding_confirm_note = None
        flag.finding_confirmed_at = None
        session.add(
            AuditLog(
                event_type="flag_overridden",
                check_run_id=run_id,
                agreement_score=flag.confidence,
                payload={
                    "flag_id": flag_id,
                    "check_result_id": result.id,
                    "instructor_id": instructor_id,
                    "reason": reason.strip(),
                    "severity": flag.severity.value,
                    "assessment_revision_before": expected_revision,
                    "assessment_revision": report.assessment_revision,
                },
            )
        )
        await session.flush()
        await aggregate_and_score(session, run_id, commit=False)
        flag_out = await _to_flag_out(
            session,
            flag,
            result,
            manuscript,
            check_run,
            assessment_revision=report.assessment_revision,
        )
        out = OverrideFlagOut(
            **flag_out.model_dump(), report=await get_report(session, run_id, instructor_id)
        )
    return out


async def confirm_finding(
    session: AsyncSession,
    flag_id: int,
    instructor_id: int,
    note: str | None,
    *,
    expected_revision: int,
) -> ConfirmFindingOut:
    """The instructor agrees the finding is a real issue (BUG-518). It keeps
    counting for readiness and replaces an earlier dismissal; one audit,
    rescore and revision, like an override."""
    _flag, initial_result, _manuscript, _run = await _scoped_flag(session, flag_id, instructor_id)
    run_id = initial_result.check_run_id
    async with assessment_write(session, run_id, instructor_id, expected_revision) as report:
        flag, result, manuscript, check_run = await _scoped_flag(session, flag_id, instructor_id)
        if flag.confirmed_citation_source:
            raise ConflictError(
                "This finding was cleared by confirming its citation source. "
                "It cannot also be confirmed as an issue."
            )
        cleaned = (note or "").strip() or None
        flag.finding_confirmed = True
        flag.finding_confirm_note = cleaned
        flag.finding_confirmed_at = datetime.now(UTC)
        flag.overridden = False
        flag.override_reason = None
        session.add(
            AuditLog(
                event_type="flag_finding_confirmed",
                check_run_id=run_id,
                agreement_score=flag.confidence,
                payload={
                    "flag_id": flag_id,
                    "check_result_id": result.id,
                    "instructor_id": instructor_id,
                    "note": cleaned,
                    "severity": flag.severity.value,
                    "assessment_revision_before": expected_revision,
                    "assessment_revision": report.assessment_revision,
                },
            )
        )
        await session.flush()
        await aggregate_and_score(session, run_id, commit=False)
        flag_out = await _to_flag_out(
            session,
            flag,
            result,
            manuscript,
            check_run,
            assessment_revision=report.assessment_revision,
        )
        out = ConfirmFindingOut(
            **flag_out.model_dump(), report=await get_report(session, run_id, instructor_id)
        )
    return out


async def confirm_citation_source(
    session: AsyncSession,
    flag_id: int,
    instructor_id: int,
    reason: str,
    *,
    expected_revision: int,
) -> ConfirmCitationSourceOut:
    """Confirm this account's source judgment with one audit, rescore and revision."""
    _flag, initial_result, _manuscript, _run = await _scoped_flag(session, flag_id, instructor_id)
    run_id = initial_result.check_run_id
    async with assessment_write(session, run_id, instructor_id, expected_revision) as report:
        flag, result, manuscript, check_run = await _scoped_flag(session, flag_id, instructor_id)
        detail = flag.detail or result.detail or {}
        key_kind, key_value = detail.get("key_kind"), detail.get("key_value")
        if detail.get("kind") != "unverifiable_not_found" or not key_kind or not key_value:
            raise ConflictError("This flag has no citation source to confirm.")
        if not await cache.citation_source_cached(session, key_kind=key_kind, key_value=key_value):
            raise NotFoundError("No cached lookup exists yet for this citation.")
        stripped_reason = reason.strip()
        flag.overridden = True
        flag.override_reason = stripped_reason
        flag.confirmed_citation_source = True
        session.add(
            AuditLog(
                event_type="citation_source_confirmed",
                check_run_id=run_id,
                payload={
                    "flag_id": flag_id,
                    "check_result_id": result.id,
                    "instructor_id": instructor_id,
                    "reason": stripped_reason,
                    "key_kind": key_kind,
                    "key_value": key_value,
                    "assessment_revision_before": expected_revision,
                    "assessment_revision": report.assessment_revision,
                },
            )
        )
        await session.flush()
        await aggregate_and_score(session, run_id, commit=False)
        flag_out = await _to_flag_out(
            session,
            flag,
            result,
            manuscript,
            check_run,
            assessment_revision=report.assessment_revision,
        )
        out = ConfirmCitationSourceOut(
            **flag_out.model_dump(), report=await get_report(session, run_id, instructor_id)
        )
    return out
