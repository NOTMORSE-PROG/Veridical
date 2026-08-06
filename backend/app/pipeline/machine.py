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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.checks.citations.verify import (
    existing_citation_integrity_result,
    run_citation_integrity_check,
)
from app.checks.consistency import run_semantic_checks_with_consistency
from app.checks.router import RouteDecision, apply_routing, route_criteria
from app.checks.rules.context import build_rule_context
from app.checks.semantic import record_ungraded
from app.checks.structural import run_structural_check
from app.config import Settings, get_settings
from app.errors import ApiDownError, FileMalformedError, QuotaExhaustedError
from app.external.http import build_http_client
from app.ingest.patterns import load_patterns
from app.ingest.service import load_raw_store
from app.llm import get_llm_client
from app.llm.base import LLMClient
from app.llm.queue import next_reset_for
from app.models.audit import AuditLog
from app.models.citation import Citation
from app.models.enums import CheckKind, CheckRunStatus, IngestStatus, ResultOutcome
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckResult, CheckRun
from app.report.service import aggregate_and_score

_STAGE_AFTER: dict[CheckRunStatus, CheckRunStatus] = {
    CheckRunStatus.queued: CheckRunStatus.ingesting,
    CheckRunStatus.ingesting: CheckRunStatus.structural,
    CheckRunStatus.structural: CheckRunStatus.semantic,
    CheckRunStatus.semantic: CheckRunStatus.integrity,
    CheckRunStatus.integrity: CheckRunStatus.aggregating,
    CheckRunStatus.aggregating: CheckRunStatus.done,
}

_STATISTICAL_FORENSICS_ORIGINALITY_NOT_IMPLEMENTED_NOTE = (
    "Citation integrity (in-text cross-match, existence, retraction checks) "
    "ran. Statistical forensics and originality/reuse checks are not "
    "implemented yet and arrive in a later milestone — honestly noted, not "
    "faked as passed. Internal agreement is already covered by "
    "self-consistency voting on every AI-graded criterion (D-006)."
)


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
) -> None:
    semantic_criterion_ids = [
        d.criterion_id for d in decisions if d.kind == CheckKind.semantic and not d.unroutable
    ]
    done_ids = await _existing_result_criterion_ids(session, check_run.id, CheckKind.semantic)
    pending = [criteria_by_id[cid] for cid in semantic_criterion_ids if cid not in done_ids]
    if pending:
        extraction = load_raw_store(settings, manuscript.id)
        try:
            await run_semantic_checks_with_consistency(
                session, check_run.id, pending, extraction, llm, settings
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
    session: AsyncSession, check_run: CheckRun, manuscript: Manuscript, settings: Settings
) -> None:
    """F5 (citation integrity, V-027/V-028/V-029) runs for real; F6
    (statistical forensics) and F7 (originality/reuse) don't exist yet —
    honestly noted, not silently skipped (charter rule 9)."""
    existing = await existing_citation_integrity_result(session, check_run.id)
    if existing is None:
        citations = list(
            (
                await session.scalars(
                    select(Citation)
                    .where(Citation.manuscript_id == manuscript.id)
                    .order_by(Citation.order_index)
                )
            ).all()
        )
        extraction = load_raw_store(settings, manuscript.id)
        patterns = load_patterns(settings.ingest_patterns_file)
        async with build_http_client(settings) as client:
            result = await run_citation_integrity_check(
                session, client, check_run.id, citations, extraction, patterns, settings
            )
        n_flags = result.detail.get("n_flags", 0) if result.detail else 0
    else:
        n_flags = existing.detail.get("n_flags", 0) if existing.detail else 0
    _record_stage(
        check_run,
        CheckRunStatus.integrity,
        status="done",
        citation_integrity_flags=n_flags,
        note=_STATISTICAL_FORENSICS_ORIGINALITY_NOT_IMPLEMENTED_NOTE,
    )


async def run_check_run(
    session: AsyncSession,
    check_run: CheckRun,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
) -> None:
    settings = settings or get_settings()
    llm = llm or get_llm_client(settings)

    manuscript = await session.get(Manuscript, check_run.manuscript_id)
    rubric = await session.scalar(
        select(Rubric)
        .where(Rubric.id == check_run.rubric_id)
        .options(selectinload(Rubric.criteria))
    )

    try:
        if check_run.status == CheckRunStatus.queued:
            check_run.started_at = check_run.started_at or datetime.now(UTC)
            check_run.status = _STAGE_AFTER[CheckRunStatus.queued]
            await session.commit()

        if check_run.status == CheckRunStatus.ingesting:
            await _run_ingesting_stage(check_run, manuscript)
            check_run.status = _STAGE_AFTER[CheckRunStatus.ingesting]
            await session.commit()

        criteria_by_id = {c.id: c for c in rubric.criteria}
        decisions = await _routed_decisions(session, check_run, rubric.criteria)

        if check_run.status == CheckRunStatus.structural:
            await _run_structural_stage(
                session, check_run, criteria_by_id, decisions, settings, manuscript
            )
            check_run.status = _STAGE_AFTER[CheckRunStatus.structural]
            await session.commit()

        if check_run.status == CheckRunStatus.semantic:
            await _run_semantic_stage(
                session, check_run, criteria_by_id, decisions, settings, manuscript, llm
            )
            check_run.status = _STAGE_AFTER[CheckRunStatus.semantic]
            await session.commit()

        if check_run.status == CheckRunStatus.integrity:
            await _run_integrity_stage(session, check_run, manuscript, settings)
            check_run.status = _STAGE_AFTER[CheckRunStatus.integrity]
            await session.commit()

        if check_run.status == CheckRunStatus.aggregating:
            await aggregate_and_score(session, check_run.id, settings)
            _record_stage(check_run, CheckRunStatus.aggregating, status="done")
            check_run.status = _STAGE_AFTER[CheckRunStatus.aggregating]
            check_run.finished_at = datetime.now(UTC)
            await session.commit()

    except PipelineBlockedError as exc:
        _record_blocked(check_run, exc.block)
        await session.commit()
    except TerminalFailure as exc:
        check_run.status = CheckRunStatus.failed
        check_run.finished_at = datetime.now(UTC)
        _record_failed(check_run, exc.code, exc.message)
        await session.commit()
    except FileMalformedError as exc:
        check_run.status = CheckRunStatus.failed
        check_run.finished_at = datetime.now(UTC)
        _record_failed(check_run, "file_malformed", str(exc))
        await session.commit()
