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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.reuse.embed import split_context
from app.config import get_settings
from app.errors import NotFoundError
from app.flags.schemas import FlagOut, PassagePairOut
from app.models.audit import AuditLog
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion
from app.models.run import CheckResult, CheckRun, Flag
from app.report.scoring import flag_ai_verdict_summary
from app.report.service import aggregate_and_score, raise_if_decided


def _passage_pair_from_detail(evidence_excerpt: str, detail: dict) -> PassagePairOut | None:
    kind = detail.get("kind") or ""
    if not kind.endswith("_passage"):
        return None
    own_before, own_after = split_context(detail.get("own_context_text", ""), evidence_excerpt)
    matched_excerpt = detail.get("matched_text", "")
    matched_before, matched_after = split_context(
        detail.get("matched_context_text", ""), matched_excerpt
    )
    return PassagePairOut(
        own_excerpt=evidence_excerpt,
        own_context_before=own_before,
        own_context_after=own_after,
        matched_ref=detail["matched_manuscript_id"],
        matched_excerpt=matched_excerpt,
        matched_context_before=matched_before,
        matched_context_after=matched_after,
        context_words_each_side=get_settings().reuse_passage_context_words,
        similarity=detail.get("similarity", 0.0),
        level="exact_duplicate" if "exact_duplicate" in kind else "high_similarity",
    )


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
        )
    ).first()
    if row is None:
        raise NotFoundError(f"No flag {flag_id}.")
    return row


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


async def _to_flag_out(
    session: AsyncSession,
    flag: Flag,
    result: CheckResult,
    manuscript: Manuscript,
    check_run: CheckRun,
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
    return FlagOut(
        id=flag.id,
        check_result_id=result.id,
        check_run_id=result.check_run_id,
        manuscript_group_label=manuscript.group_label,
        check_kind=result.kind.value,
        criterion_text=criterion_text,
        severity=flag.severity.value,
        confidence=float(flag.confidence) if flag.confidence is not None else None,
        evidence_excerpt=flag.evidence_excerpt,
        page_anchor=flag.page_anchor,
        annotation=flag.annotation,
        overridden=flag.overridden,
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
        ai_reasoning=_distinct_reasoning(detail, flag.evidence_excerpt),
        llm_mode=check_run.llm_mode.value,
        passage_pair=_passage_pair_from_detail(flag.evidence_excerpt, detail),
        first_upload_context=bool(detail.get("first_upload_context")),
    )


async def get_flag(session: AsyncSession, flag_id: int, instructor_id: int) -> FlagOut:
    flag, result, manuscript, check_run = await _scoped_flag(session, flag_id, instructor_id)
    return await _to_flag_out(session, flag, result, manuscript, check_run)


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
    await session.refresh(flag)
    return await _to_flag_out(session, flag, result, manuscript, check_run)


async def override_flag(
    session: AsyncSession, flag_id: int, instructor_id: int, reason: str
) -> tuple[FlagOut, int]:
    """Overriding a flag never destroys the original AI/check finding
    (ticket AC) — `evidence_excerpt`/`page_anchor`/the parent
    `check_result.detail` are untouched; only `overridden`/
    `override_reason` change, which is what the scoring engine already
    watches (`_flag_deduction`, V-019). Returns the flag plus its
    `check_run_id` so the router can hand back a fresh `ReportOut` in the
    SAME response (ticket AC: "recomputes score/status immediately").

    V-038: blocked once the report has been decided — a score-affecting
    mutation on a report a human already signed off on must go through an
    explicit reopen first (see `app.report.service.raise_if_decided`)."""
    flag, result, manuscript, check_run = await _scoped_flag(session, flag_id, instructor_id)
    await raise_if_decided(session, result.check_run_id)
    flag.overridden = True
    flag.override_reason = reason.strip()
    session.add(
        AuditLog(
            event_type="flag_overridden",
            check_run_id=result.check_run_id,
            agreement_score=flag.confidence,
            payload={
                "flag_id": flag_id,
                "check_result_id": result.id,
                "instructor_id": instructor_id,
                "reason": reason.strip(),
                "severity": flag.severity.value,
            },
        )
    )
    await session.commit()
    await aggregate_and_score(session, result.check_run_id)
    await session.refresh(flag)
    return await _to_flag_out(session, flag, result, manuscript, check_run), result.check_run_id
