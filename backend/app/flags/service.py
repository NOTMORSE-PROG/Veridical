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
from app.errors import ConflictError, NotFoundError
from app.external import cache
from app.flags.schemas import CitationSourceKeyOut, FlagOut, PassagePairOut
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


def _citation_source_key(detail: dict) -> CitationSourceKeyOut | None:
    """BUG-078: `verify.py` sets `key_kind`/`key_value` on a
    `unverifiable_not_found` flag's detail only when a real DOI/ISBN/title
    existed to key the lookup on (never for the "no identifier at all"
    case) -- presence here is exactly what gates the "Confirm this source"
    button on screen 4i (`ui-designer` spec, 2026-08-24)."""
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
        citation_source_key=_citation_source_key(detail),
        confirmed_citation_source=flag.confirmed_citation_source,
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


async def confirm_citation_source(
    session: AsyncSession, flag_id: int, instructor_id: int, reason: str
) -> tuple[FlagOut, int]:
    """BUG-078/FEATURES.md §9: an "unverifiable, not found" citation flag
    can be manually confirmed legitimate -- e.g. a real local/Philippine
    source the four providers don't index. Two effects: (1) THIS flag is
    overridden, same mechanics as `override_flag` (audit log,
    `raise_if_decided` gate, live rescore, original finding never
    destroyed) -- `flag.confirmed_citation_source` additionally records
    that this path (not an ordinary override) is what resolved it, so the
    terminal banner can say something true instead of "you overrode this
    finding" (`ui-designer` spec, 2026-08-24); (2) the underlying
    `citation_cache` row is marked `instructor_confirmed` -- a DURABLE,
    CROSS-MANUSCRIPT mark (`citation_cache` is keyed by source identity —
    DOI/ISBN/title — not by instructor or manuscript), so any future check
    run, including another instructor's manuscript citing the identical
    source, is never re-flagged either (FEATURES.md §9's own wording:
    "cache instructor's manual confirmations so the same source isn't
    re-flagged").

    `reason` is instructor-authored ("Where you verified this") -- required,
    same discipline as `override_flag`'s reason, and if anything a HIGHER
    bar is warranted here (durable, cross-account), never a lower one.

    Only reachable for a flag whose detail carries `key_kind`/`key_value`
    (set by `verify.py` only when a real DOI/ISBN/title existed to key the
    lookup on) — anything else is a `ConflictError`, never a silent no-op.

    Commit ORDER matters (`backend-critic`, BUG-078 review, live-
    reproduced): the flag/audit-log write commits FIRST, and only then is
    `citation_cache` marked confirmed (its own separate commit) -- the
    reverse order left a window where a crash between the two commits
    would durably silence this source everywhere, forever, with no
    `Flag.overridden` and no audit trail at all. This order's own worst
    case (a crash after the first commit) is strictly safer: the flag is
    honestly resolved with a real audit trail, and the source just isn't
    globally suppressed yet -- recoverable by confirming again, which is
    idempotent."""
    flag, result, manuscript, check_run = await _scoped_flag(session, flag_id, instructor_id)
    await raise_if_decided(session, result.check_run_id)
    detail = flag.detail or result.detail or {}
    key_kind = detail.get("key_kind")
    key_value = detail.get("key_value")
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
            check_run_id=result.check_run_id,
            payload={
                "flag_id": flag_id,
                "check_result_id": result.id,
                "instructor_id": instructor_id,
                "reason": stripped_reason,
                "key_kind": key_kind,
                "key_value": key_value,
            },
        )
    )
    await session.commit()
    await cache.confirm_citation_source(session, key_kind=key_kind, key_value=key_value)
    await aggregate_and_score(session, result.check_run_id)
    await session.refresh(flag)
    return await _to_flag_out(session, flag, result, manuscript, check_run), result.check_run_id
