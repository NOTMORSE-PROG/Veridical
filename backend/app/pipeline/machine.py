"""The check-run state machine (ENGINEERING.md §4).

`run_check_run` is the single entry point: given a `CheckRun` row in ANY
non-terminal status, it walks every remaining stage in one call (queued
-> ingesting -> structural -> semantic -> integrity -> aggregating ->
done), stopping only on a terminal failure or a `PipelineBlockedError`
(quota_exhausted / api_down — parked, not failed, D-001's resumability
requirement). Every stage's own work is idempotent: it queries already-
persisted `check_result` rows and skips criteria that already have one,
so calling this again after a crash (or a block clears) resumes exactly
where it left off — no duplicate LLM calls for completed work (ticket
AC).
"""

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit.service import write_audit_event
from app.checks.agreement.service import (
    existing_internal_agreement_result,
    run_internal_agreement_check,
)
from app.checks.citations.verify import (
    existing_citation_integrity_result,
    run_citation_integrity_check,
)
from app.checks.consistency import run_semantic_checks_with_consistency
from app.checks.forensics.service import (
    existing_statistical_forensics_result,
    run_statistical_forensics_check,
)
from app.checks.reuse.service import (
    existing_originality_reuse_result,
    run_originality_reuse_check,
)
from app.checks.router import RouteDecision, apply_routing, route_criteria
from app.checks.rules.context import build_rule_context
from app.checks.semantic import record_ungraded
from app.checks.structural import run_structural_check
from app.config import Settings, get_settings
from app.errors import ApiDownError, FileMalformedError, QuotaExhaustedError
from app.external.http import build_http_client
from app.ingest.patterns import load_patterns
from app.ingest.service import load_raw_store_async
from app.llm import get_llm_client_for
from app.llm.base import LLMClient
from app.llm.queue import next_reset_for
from app.models.audit import AuditLog
from app.models.citation import Citation
from app.models.enums import CheckKind, CheckRunStatus, IngestStatus, ResultOutcome
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckResult, CheckRun, ReadinessReport
from app.report.service import aggregate_and_score

_STAGE_AFTER: dict[CheckRunStatus, CheckRunStatus] = {
    CheckRunStatus.queued: CheckRunStatus.ingesting,
    CheckRunStatus.ingesting: CheckRunStatus.structural,
    CheckRunStatus.structural: CheckRunStatus.semantic,
    CheckRunStatus.semantic: CheckRunStatus.integrity,
    CheckRunStatus.integrity: CheckRunStatus.aggregating,
    CheckRunStatus.aggregating: CheckRunStatus.done,
}

_INTEGRITY_STAGE_NOTE = (
    "Internal agreement (intent vs. outcome), citation integrity "
    "(cross-match, existence, retraction, claim-support), statistical "
    "forensics (GRIM/GRIMMER, p-value recalculation, sanity checks), and "
    "originality/reuse (archive similarity) all ran."
)


async def _finish_cancel_if_requested(session: AsyncSession, check_run: CheckRun) -> bool:
    """Stop at a persistence boundary without presenting partial work as done.

    The cancellation API writes only `cancel_requested_at` for an active run.
    Refreshing that separate column prevents a concurrent request from being
    lost when this worker commits its own stage transition.
    """
    locked = await session.scalar(
        select(CheckRun)
        .where(CheckRun.id == check_run.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        return True
    check_run = locked
    if check_run.status == CheckRunStatus.cancelled:
        return True
    if check_run.cancel_requested_at is None:
        await session.commit()
        return False

    current_stage = check_run.status
    stages = (check_run.stage_status or {}).get("stages", {})
    stage_detail = stages.get(current_stage.value, {}) if isinstance(stages, dict) else {}
    current_stage_finished = isinstance(stage_detail, dict) and stage_detail.get("status") == "done"
    if current_stage == CheckRunStatus.queued:
        stopped_before = CheckRunStatus.ingesting.value
    elif current_stage_finished and current_stage in _STAGE_AFTER:
        stopped_before = _STAGE_AFTER[current_stage].value
    else:
        stopped_before = current_stage.value
    # Aggregation may have created a report in the same interval in which the
    # instructor requested cancellation. Keep intermediate results and every
    # audit row, but never leave a terminal report reachable for a cancelled
    # run (V-071 AC12: a half-run must not look complete).
    await session.execute(
        delete(ReadinessReport).where(ReadinessReport.check_run_id == check_run.id)
    )
    check_run.status = CheckRunStatus.cancelled
    check_run.finished_at = datetime.now(UTC)
    status = dict(check_run.stage_status or {})
    status["cancellation"] = {
        "status": "cancelled",
        "requested_at": check_run.cancel_requested_at.isoformat(),
        "stopped_before": stopped_before,
    }
    check_run.stage_status = status
    await write_audit_event(
        session,
        event_type="check_run_cancelled",
        check_run_id=check_run.id,
        payload={"stopped_before": stopped_before},
    )
    await session.commit()
    return True


class CancellationAccepted(Exception):
    """Internal control flow after a persisted terminal cancellation."""


class ClaimLost(Exception):
    """Internal control flow (BUG-144, `backend-critic` follow-up finding):
    raised when `heartbeat()` reports the claim was reassigned to another
    driver -- this one made no progress for `pipeline_claim_stale_seconds`
    and someone else now legitimately owns the run. Same "another driver
    already has this, stop silently" shape as `CancellationAccepted`, NOT
    a real failure: without this, the original finding was that a lost
    heartbeat was silently swallowed (its `None` return discarded) and the
    stale holder kept right on working, which the fencing-token release
    logic alone does not prevent -- it only stops the stale holder's
    eventual cleanup from stealing the claim BACK, not the wasted/
    colliding work it does in between."""


CancellationBoundary = Callable[[], Awaitable[None]]
# BUG-144: same shape as `CancellationBoundary`, distinct name -- a
# zero-arg async callback, but for claim-refresh rather than cancellation.
# Raises `ClaimLost` if the claim was reassigned (see `ClaimLost` above).
Heartbeat = Callable[[], Awaitable[None]]


async def _transition_after_boundary(
    session: AsyncSession,
    check_run: CheckRun,
    completed_stage: CheckRunStatus,
) -> bool:
    """Persist one safe unit, then atomically advance only if not cancelled.

    The conditional UPDATE is the database winner for the completion/cancel
    race. A request timestamp written first prevents the next stage (including
    terminal DONE) from being entered. A terminal transition written first
    makes the cancellation endpoint return Conflict without persisting a
    misleading request.
    """
    await session.commit()
    if await _finish_cancel_if_requested(session, check_run):
        return False

    next_stage = _STAGE_AFTER[completed_stage]
    values: dict[str, Any] = {"status": next_stage}
    now = datetime.now(UTC)
    if completed_stage == CheckRunStatus.queued:
        values["started_at"] = func.coalesce(CheckRun.started_at, now)
    if next_stage == CheckRunStatus.done:
        values["finished_at"] = now

    advanced = (
        await session.execute(
            update(CheckRun)
            .where(
                CheckRun.id == check_run.id,
                CheckRun.status == completed_stage,
                CheckRun.cancel_requested_at.is_(None),
            )
            .values(**values)
            .returning(CheckRun.id)
        )
    ).first()
    if advanced is None:
        await session.refresh(check_run)
        if check_run.cancel_requested_at is not None:
            await _finish_cancel_if_requested(session, check_run)
        return False

    await session.commit()
    await session.refresh(check_run)
    return True


async def _exception_target_if_not_cancelled(
    session: AsyncSession, check_run: CheckRun
) -> CheckRun | None:
    """Lock the run after an exception and honor the terminal-state winner.

    A cancellation request may commit while a stage is unwinding through an
    exception. Roll back that stage's unsafe unit, then hold the row lock until
    cancellation is finalized or the failure/blocked state is persisted. This
    prevents every exception exit from overwriting an accepted instructor stop.
    """
    check_run_id = check_run.id
    await session.rollback()
    locked = await session.scalar(
        select(CheckRun)
        .where(CheckRun.id == check_run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        return None
    if locked.status == CheckRunStatus.cancelled:
        await session.commit()
        return None
    if locked.cancel_requested_at is not None:
        await _finish_cancel_if_requested(session, locked)
        return None
    if locked.status not in _STAGE_AFTER:
        await session.commit()
        return None
    return locked


@dataclass(frozen=True)
class StageBlock:
    """Why a run is PARKED (not failed) and when it's worth retrying —
    D-001's resumability requirement, applied to both quota exhaustion
    (a precise reset time) and a transient api_down (a fixed backoff,
    since there's no equivalent precise "back online at" signal)."""

    code: str
    message: str
    resume_at: datetime | None


class PipelineBlockedError(Exception):
    def __init__(self, block: StageBlock):
        super().__init__(block.message)
        self.block = block


class TerminalFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _stage_status_copy(check_run: CheckRun) -> dict[str, Any]:
    """A DEEP copy of the current stage_status, safe to mutate freely.

    Critical: SQLAlchemy's plain (non-Mutable-tracked) JSONB column skips
    the UPDATE on commit if the newly-assigned value compares `==` equal
    to the value it already holds — a real bug found live (V-018's
    Playwright smoke test): mutating `check_run.stage_status` IN PLACE
    before reassigning made the "old" and "new" values equal by content
    (they were the same object plus a copy of it), so every stage after
    the first silently failed to persist even though `check_run.status`
    kept advancing correctly and every check_result was saved. Copying
    BEFORE mutating keeps the ORM-tracked original genuinely different
    from the new value, so the dirty-flag fires and the UPDATE ships.
    """
    current = check_run.stage_status
    return {"stages": {}} if current is None else copy.deepcopy(current)


def _record_stage(check_run: CheckRun, stage: CheckRunStatus, **fields: Any) -> None:
    status = _stage_status_copy(check_run)
    stages = status.setdefault("stages", {})
    entry = stages.setdefault(stage.value, {})
    entry.update(fields)
    status.pop("blocked", None)
    check_run.stage_status = status


def _record_blocked(check_run: CheckRun, block: StageBlock) -> None:
    status = _stage_status_copy(check_run)
    status["blocked"] = {
        "code": block.code,
        "message": block.message,
        "resume_at": block.resume_at.isoformat() if block.resume_at else None,
    }
    check_run.stage_status = status


def _record_failed(check_run: CheckRun, code: str, message: str) -> None:
    status = _stage_status_copy(check_run)
    status["failed"] = {"code": code, "message": message}
    check_run.stage_status = status


def blocked_info(check_run: CheckRun) -> dict[str, Any] | None:
    return (check_run.stage_status or {}).get("blocked")


def is_blocked(check_run: CheckRun, *, now: datetime | None = None) -> bool:
    block = blocked_info(check_run)
    if block is None:
        return False
    resume_at = block.get("resume_at")
    if resume_at is None:
        return True  # no known resume time (e.g. still ingesting) — never auto-poll-ready
    now = now or datetime.now(UTC)
    return datetime.fromisoformat(resume_at) > now


async def _existing_result_criterion_ids(
    session: AsyncSession, check_run_id: int, kind: CheckKind
) -> set[int]:
    ids = await session.scalars(
        select(CheckResult.criterion_id).where(
            CheckResult.check_run_id == check_run_id, CheckResult.kind == kind
        )
    )
    return {i for i in ids if i is not None}


async def _routed_decisions(
    session: AsyncSession, check_run: CheckRun, criteria: list[Any]
) -> list[RouteDecision]:
    """Routing (V-015) is pure and cheap to recompute every call, but its
    SIDE EFFECT (the audit_log row + persisted not_applicable results for
    unroutable criteria) must only ever happen ONCE per check_run."""
    decisions = route_criteria(criteria)
    already_routed = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.check_run_id == check_run.id, AuditLog.event_type == "criterion_routing")
    )
    if not already_routed:
        await apply_routing(session, check_run.id, decisions)
    return decisions


async def _run_ingesting_stage(check_run: CheckRun, manuscript: Manuscript) -> None:
    if manuscript.ingest_status == IngestStatus.failed:
        raise TerminalFailure(
            "file_malformed", "The manuscript failed ingestion and cannot be checked."
        )
    if manuscript.ingest_status != IngestStatus.done:
        # Ingestion normally completes synchronously at upload time
        # (V-004/V-008); this guards the (currently theoretical) case of
        # a check requested before it finished.
        raise PipelineBlockedError(
            StageBlock(
                code="ingesting", message="Manuscript is still being ingested.", resume_at=None
            )
        )
    _record_stage(check_run, CheckRunStatus.ingesting, status="done")


async def _run_structural_stage(
    session: AsyncSession,
    check_run: CheckRun,
    criteria_by_id: dict[int, Any],
    decisions: list[RouteDecision],
    settings: Settings,
    manuscript: Manuscript,
) -> None:
    structural_decisions = [
        d for d in decisions if d.kind == CheckKind.structural and not d.unroutable
    ]
    done_ids = await _existing_result_criterion_ids(session, check_run.id, CheckKind.structural)
    pending = [d for d in structural_decisions if d.criterion_id not in done_ids]
    if pending:
        ctx = await build_rule_context(session, manuscript, settings)
        for decision in pending:
            criterion = criteria_by_id[decision.criterion_id]
            await run_structural_check(
                session, check_run.id, criterion, criterion.id, decision, ctx
            )
    _record_stage(
        check_run, CheckRunStatus.structural, status="done", n_criteria=len(structural_decisions)
    )


async def _run_semantic_stage(
    session: AsyncSession,
    check_run: CheckRun,
    criteria_by_id: dict[int, Any],
    decisions: list[RouteDecision],
    settings: Settings,
    manuscript: Manuscript,
    llm: LLMClient,
    cancellation_boundary: CancellationBoundary,
) -> None:
    semantic_criterion_ids = [
        d.criterion_id for d in decisions if d.kind == CheckKind.semantic and not d.unroutable
    ]
    done_ids = await _existing_result_criterion_ids(session, check_run.id, CheckKind.semantic)
    pending = [criteria_by_id[cid] for cid in semantic_criterion_ids if cid not in done_ids]
    if pending:
        extraction = await load_raw_store_async(settings, manuscript.id)
        try:
            await run_semantic_checks_with_consistency(
                session,
                check_run.id,
                pending,
                extraction,
                llm,
                settings,
                cancellation_boundary,
            )
        except QuotaExhaustedError as exc:
            # AVAILABILITY FLOOR (V-050): the day's AI budget resets at
            # midnight Pacific, which can be AFTER the defense. Parking the
            # run here means the instructor gets nothing at all, so instead
            # the run FINISHES: everything deterministic is already done, and
            # the criteria the AI never reached are handed to the instructor
            # as an honest `quota_exhausted` state (never a pass, never a
            # guess — charter rules 1 and 9). Re-running later fills the AI
            # grades in for free, since completed criteria are skipped and
            # cached responses cost no quota (D-011).
            if not settings.pipeline_degrade_on_quota:
                raise PipelineBlockedError(
                    StageBlock(
                        code="quota_exhausted",
                        message=str(exc),
                        resume_at=next_reset_for(settings.llm_quota_reset_timezone),
                    )
                ) from exc
            ungraded = await _degrade_pending_semantic(
                session,
                check_run,
                criteria_by_id,
                semantic_criterion_ids,
                outcome=ResultOutcome.quota_exhausted,
                reason=settings.pipeline_quota_degraded_reason,
            )
            # Structured, not a freeform note: the frontend renders this
            # instructor-facing (screen 4g), and a raw exception string
            # embedded in a note could say anything — a real charter-9
            # honesty risk (V-055 review). `degraded_count`/`degraded_code`
            # are only set on an actual degradation, never on a clean run.
            stage_fields: dict[str, Any] = {
                "status": "done",
                "n_criteria": len(semantic_criterion_ids),
            }
            if ungraded > 0:
                stage_fields["degraded_count"] = ungraded
                stage_fields["degraded_code"] = "quota_exhausted"
            _record_stage(check_run, CheckRunStatus.semantic, **stage_fields)
            return
        except ApiDownError as exc:
            retry_seconds = settings.pipeline_api_down_retry_seconds
            resume_at = datetime.now(UTC) + timedelta(seconds=retry_seconds)
            raise PipelineBlockedError(
                StageBlock(code="api_down", message=str(exc), resume_at=resume_at)
            ) from exc
    _record_stage(
        check_run, CheckRunStatus.semantic, status="done", n_criteria=len(semantic_criterion_ids)
    )


async def _degrade_pending_semantic(
    session: AsyncSession,
    check_run: CheckRun,
    criteria_by_id: dict[int, Any],
    semantic_criterion_ids: list[int],
    *,
    outcome: ResultOutcome,
    reason: str,
) -> int:
    """Write an honest non-verdict for every semantic criterion still without
    a result. Re-queried (not reused) because the grading pass may have
    persisted some results before the budget ran out — those keep their real
    AI grades."""
    done_ids = await _existing_result_criterion_ids(session, check_run.id, CheckKind.semantic)
    pending = [criteria_by_id[cid] for cid in semantic_criterion_ids if cid not in done_ids]
    await record_ungraded(session, check_run.id, pending, outcome=outcome, reason=reason)
    return len(pending)


async def _run_integrity_stage(
    session: AsyncSession,
    check_run: CheckRun,
    manuscript: Manuscript,
    settings: Settings,
    llm: LLMClient,
    cancellation_boundary: CancellationBoundary,
) -> None:
    """F4 (internal agreement, V-034/V-035), F5 (citation integrity,
    V-027/V-028/V-029/V-030), F6 (statistical forensics, V-031/V-032/
    V-033), and F7 (originality/reuse, V-036/V-037) all run for real."""
    agreement_existing = await existing_internal_agreement_result(session, check_run.id)
    citation_existing = await existing_citation_integrity_result(session, check_run.id)
    forensics_existing = await existing_statistical_forensics_result(session, check_run.id)
    reuse_existing = await existing_originality_reuse_result(session, check_run.id)

    # BUG-152: a resumed run that reaches this stage with all four checks
    # already done (a crash between this stage's own `_record_stage` and
    # the transition to `aggregating` that follows it, the same window
    # BUG-151's `verdict_computed` dedup guards) has no real use for the
    # extraction at all -- reading and fully re-validating it (a real,
    # sometimes-durable-storage-backed read) is pure waste on the one path
    # most likely to be under load. Same `if pending:` guard
    # `_run_semantic_stage` already uses above.
    extraction = None
    if None in (agreement_existing, citation_existing, forensics_existing, reuse_existing):
        extraction = await load_raw_store_async(settings, manuscript.id)

    if agreement_existing is None:
        agreement_result = await run_internal_agreement_check(
            session, llm, check_run.id, extraction, settings
        )
        agreement_flags = (
            agreement_result.detail.get("n_flags", 0) if agreement_result.detail else 0
        )
    else:
        agreement_flags = (
            agreement_existing.detail.get("n_flags", 0) if agreement_existing.detail else 0
        )
    await cancellation_boundary()

    if citation_existing is None:
        citations = list(
            (
                await session.scalars(
                    select(Citation)
                    .where(Citation.manuscript_id == manuscript.id)
                    .order_by(Citation.order_index)
                )
            ).all()
        )
        patterns = load_patterns(settings.ingest_patterns_file)
        async with build_http_client(settings) as client:
            citation_result = await run_citation_integrity_check(
                session, client, check_run.id, citations, extraction, patterns, settings, llm
            )
        citation_flags = citation_result.detail.get("n_flags", 0) if citation_result.detail else 0
    else:
        citation_flags = (
            citation_existing.detail.get("n_flags", 0) if citation_existing.detail else 0
        )
    await cancellation_boundary()

    if forensics_existing is None:
        forensics_result = await run_statistical_forensics_check(session, check_run.id, extraction)
        forensics_flags = (
            forensics_result.detail.get("n_flags", 0) if forensics_result.detail else 0
        )
    else:
        forensics_flags = (
            forensics_existing.detail.get("n_flags", 0) if forensics_existing.detail else 0
        )
    await cancellation_boundary()

    if reuse_existing is None:
        reuse_result = await run_originality_reuse_check(
            session,
            manuscript.id,
            check_run.id,
            extraction,
            settings,
            instructor_id=manuscript.instructor_id,
            content_hash=manuscript.content_hash,
        )
        reuse_detail = reuse_result.detail or {}
    else:
        reuse_detail = reuse_existing.detail or {}
    await cancellation_boundary()
    reuse_flags = reuse_detail.get("n_flags", 0)
    # Cold-start disclosure (V-037 ticket AC): how many previously
    # processed manuscripts this run was actually compared against — shown
    # even at 0 (charter rule 9: honest about growing coverage).
    archive_size_n = reuse_detail.get("archive_size_n", 0)

    _record_stage(
        check_run,
        CheckRunStatus.integrity,
        status="done",
        internal_agreement_flags=agreement_flags,
        citation_integrity_flags=citation_flags,
        statistical_forensics_flags=forensics_flags,
        originality_reuse_flags=reuse_flags,
        originality_reuse_archive_size_n=archive_size_n,
        note=_INTEGRITY_STAGE_NOTE,
    )


async def run_check_run(
    session: AsyncSession,
    check_run: CheckRun,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    heartbeat: Heartbeat | None = None,
) -> None:
    """`heartbeat` (BUG-144, `backend-critic` finding): an optional
    zero-arg callback `worker.py` wires up to refresh `check_run.claimed_at`
    -- called from the SAME per-batch checkpoints `cancellation_boundary`
    already uses below, so a genuinely-alive-but-slow run (many criteria,
    LLM retries) keeps its claim fresh as it makes progress, and only a
    run that's made NO progress at all for `pipeline_claim_stale_seconds`
    is ever treated as abandoned. `None` (every caller except `worker.py`,
    including every test) means no claim exists to refresh -- a no-op."""
    settings = settings or get_settings()

    if await _finish_cancel_if_requested(session, check_run):
        return

    manuscript = await session.get(Manuscript, check_run.manuscript_id)
    # V-052 (BYOK): resolved AFTER `manuscript` so the instructor's own
    # Gemini key (falling back to the shared pool on quota exhaustion) is
    # used instead of always spending the shared key -- `llm` stays
    # injectable (tests, and the vision-pass path in ingest/service.py
    # which resolves its own client earlier, before a CheckRun exists).
    llm = llm or await get_llm_client_for(
        session, settings, manuscript.instructor_id if manuscript is not None else None
    )
    rubric = await session.scalar(
        select(Rubric)
        .where(Rubric.id == check_run.rubric_id)
        .options(selectinload(Rubric.criteria))
    )

    async def cancellation_boundary() -> None:
        if heartbeat is not None:
            await heartbeat()
        if await _finish_cancel_if_requested(session, check_run):
            raise CancellationAccepted

    try:
        if check_run.status == CheckRunStatus.queued and not await _transition_after_boundary(
            session, check_run, CheckRunStatus.queued
        ):
            return

        if check_run.status == CheckRunStatus.ingesting:
            await _run_ingesting_stage(check_run, manuscript)
            if not await _transition_after_boundary(session, check_run, CheckRunStatus.ingesting):
                return

        criteria_by_id = {c.id: c for c in rubric.criteria}
        decisions = await _routed_decisions(session, check_run, rubric.criteria)

        if check_run.status == CheckRunStatus.structural:
            await _run_structural_stage(
                session, check_run, criteria_by_id, decisions, settings, manuscript
            )
            if not await _transition_after_boundary(session, check_run, CheckRunStatus.structural):
                return

        if check_run.status == CheckRunStatus.semantic:
            await _run_semantic_stage(
                session,
                check_run,
                criteria_by_id,
                decisions,
                settings,
                manuscript,
                llm,
                cancellation_boundary,
            )
            if not await _transition_after_boundary(session, check_run, CheckRunStatus.semantic):
                return

        if check_run.status == CheckRunStatus.integrity:
            await _run_integrity_stage(
                session,
                check_run,
                manuscript,
                settings,
                llm,
                cancellation_boundary,
            )
            if not await _transition_after_boundary(session, check_run, CheckRunStatus.integrity):
                return

        if check_run.status == CheckRunStatus.aggregating:
            await aggregate_and_score(session, check_run.id, settings)
            _record_stage(check_run, CheckRunStatus.aggregating, status="done")
            await _transition_after_boundary(session, check_run, CheckRunStatus.aggregating)

    except CancellationAccepted:
        return
    except ClaimLost:
        # BUG-144: another driver now legitimately owns this run (see
        # `ClaimLost`'s own docstring) -- back off silently, the same as
        # an accepted cancellation. Whatever that driver has or hasn't
        # persisted yet is its own concern; this call must not touch
        # `check_run`'s status at all.
        return
    except PipelineBlockedError as exc:
        # BUG-032 finding: if the exception that landed us here came from a
        # DB-level failure mid-flush (an IntegrityError, a race with the
        # poll loop), the session is left needing a rollback before it can
        # commit again — otherwise THIS commit raises PendingRollbackError,
        # propagating back out uncaught and re-swallowing the original bug.
        # `rollback()` expires check_run's attributes; the async ORM can't
        # lazy-load them on a plain sync attribute read afterward (raises
        # MissingGreenlet), so an explicit refresh is required before
        # `_record_blocked` touches `check_run.stage_status`.
        target = await _exception_target_if_not_cancelled(session, check_run)
        if target is None:
            return
        _record_blocked(target, exc.block)
        await session.commit()
    except TerminalFailure as exc:
        target = await _exception_target_if_not_cancelled(session, check_run)
        if target is None:
            return
        target.status = CheckRunStatus.failed
        target.finished_at = datetime.now(UTC)
        _record_failed(target, exc.code, exc.message)
        await session.commit()
    except FileMalformedError as exc:
        target = await _exception_target_if_not_cancelled(session, check_run)
        if target is None:
            return
        target.status = CheckRunStatus.failed
        target.finished_at = datetime.now(UTC)
        _record_failed(target, "file_malformed", str(exc))
        await session.commit()
    except Exception as exc:
        # BUG-032: this runs inside a Starlette BackgroundTask (worker.py),
        # which does not propagate exceptions anywhere — no client-visible
        # error, no DB write. Without this catch-all, any stage-level bug
        # (a missing data_dir cache file, an unhandled library error) leaves
        # the row frozen at its last successful stage forever, reading as
        # "in progress" with no way for the instructor to tell it apart from
        # a run that's merely slow (charter rule 9: a stalled run must say
        # so, not stay silent). Same generic-but-honest catch-all pattern as
        # IngestFailureReason (models/enums.py) — one bucket, not one per
        # exception type.
        target = await _exception_target_if_not_cancelled(session, check_run)
        if target is None:
            return
        target.status = CheckRunStatus.failed
        target.finished_at = datetime.now(UTC)
        _record_failed(target, "unexpected_error", str(exc))
        await session.commit()
