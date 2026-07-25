"""Confidence-based escalation (V-022/V-023, F3.5, D-006): the ONLY place
that decides whether a self-consistency vote (V-022) is trusted enough to
auto-score, or must go to the instructor instead (charter rule 1 — a
low-confidence verdict is escalated, never guessed).

`gate_vote` is a pure function so the threshold decision is unit-testable
in isolation from any LLM call; `app.checks.consistency` calls it at
persist time. The rest of this module (escalated-panel listing +
resolution) is the consumer-facing half of the same ticket (V-023).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.errors import ConflictError, NotFoundError
from app.models.audit import AuditLog
from app.models.enums import ResultOutcome
from app.models.rubric import Criterion
from app.models.run import CheckResult

_SCORE_BY_VERDICT = {"pass": 100.0, "partial": 50.0, "fail": 0.0}
_OUTCOME_BY_VERDICT = {
    "pass": ResultOutcome.passed,
    "partial": ResultOutcome.passed,
    "fail": ResultOutcome.failed,
}

# Instructor's three resolution choices (ticket): accept whatever the AI's
# own majority vote was, or override to a specific verdict outright.
RESOLUTION_ACCEPT_MAJORITY = "accept_majority"
RESOLUTION_MARK_PASS = "mark_pass"
RESOLUTION_MARK_FAIL = "mark_fail"
_VALID_RESOLUTIONS = {RESOLUTION_ACCEPT_MAJORITY, RESOLUTION_MARK_PASS, RESOLUTION_MARK_FAIL}


def gate_vote(
    majority_verdict: str | None, agreement: float, settings: Settings | None = None
) -> tuple[ResultOutcome, float | None]:
    """Turns a self-consistency vote into a persistable (outcome, score).

    No majority at all (a genuine 3-way pass/partial/fail split) always
    escalates — that's a structural fact about the vote, not a policy
    choice, so it isn't gated by the threshold (ticket edge case: "never
    majority-by-technicality"). A real majority still escalates if its
    agreement falls below the configured threshold (default 1.0 — ANY
    disagreement escalates until golden-set evidence justifies loosening
    it, D-012).
    """
    settings = settings or get_settings()
    if majority_verdict is None:
        return ResultOutcome.escalated, None
    if agreement < settings.escalation_agreement_threshold:
        return ResultOutcome.escalated, None
    return _OUTCOME_BY_VERDICT[majority_verdict], _SCORE_BY_VERDICT[majority_verdict]


@dataclass(frozen=True)
class EscalatedItem:
    check_result_id: int
    criterion_id: int
    criterion_text: str
    weight: float
    agreement: float | None
    votes: list[str | None]
    reason: str | None
    detail: dict[str, Any]


async def list_escalated(session: AsyncSession, check_run_id: int) -> list[EscalatedItem]:
    """Screen 4h's "AI wasn't sure — review these" panel: every semantic
    criterion still sitting at `escalated` for this run, oldest first so
    the panel order is stable across reloads."""
    rows = (
        await session.execute(
            select(CheckResult, Criterion)
            .join(Criterion, Criterion.id == CheckResult.criterion_id)
            .where(
                CheckResult.check_run_id == check_run_id,
                CheckResult.outcome == ResultOutcome.escalated,
            )
            .order_by(CheckResult.created_at)
        )
    ).all()
    items = []
    for result, criterion in rows:
        detail = result.detail or {}
        items.append(
            EscalatedItem(
                check_result_id=result.id,
                criterion_id=criterion.id,
                criterion_text=criterion.text,
                weight=float(criterion.weight),
                agreement=detail.get("agreement"),
                votes=detail.get("votes", []),
                reason=detail.get("reason"),
                detail=detail,
            )
        )
    return items


async def resolve_escalation(
    session: AsyncSession,
    check_run_id: int,
    check_result_id: int,
    instructor_id: int,
    resolution: str,
    reason: str,
) -> CheckResult:
    """One instructor decision on one escalated criterion (ticket AC): the
    ONLY way an escalated result ever becomes a score contribution again —
    never automatic (charter rule 1). The original AI vote is preserved
    (never overwritten) so the report can show "AI said X · instructor
    resolved to Y — reason" side by side; only `outcome`/`score` change to
    reflect the human decision, which is what `aggregate_and_score`
    actually counts.
    """
    # Reason presence is primarily enforced by the router's request schema
    # (`Field(min_length=1)`, CODING.md §1: routers validate); this is a
    # defensive second check for any other caller of this service function.
    if not reason or not reason.strip():
        raise ConflictError("A reason is required to resolve an escalated item.")
    if resolution not in _VALID_RESOLUTIONS:
        raise ConflictError(f"Unknown resolution {resolution!r}.")

    result = await session.get(CheckResult, check_result_id)
    if result is None or result.check_run_id != check_run_id:
        raise NotFoundError(f"No check result {check_result_id} on check run {check_run_id}.")
    if result.outcome != ResultOutcome.escalated:
        raise ConflictError("This criterion isn't escalated — nothing to resolve.")

    detail = dict(result.detail or {})
    majority_verdict = detail.get("verdict")

    if resolution == RESOLUTION_ACCEPT_MAJORITY:
        if majority_verdict is None:
            raise ConflictError(
                "The AI never reached a majority verdict on this criterion — "
                "choose mark_pass or mark_fail instead."
            )
        new_outcome = _OUTCOME_BY_VERDICT[majority_verdict]
        new_score = _SCORE_BY_VERDICT[majority_verdict]
    elif resolution == RESOLUTION_MARK_PASS:
        new_outcome, new_score = ResultOutcome.passed, 100.0
    else:
        new_outcome, new_score = ResultOutcome.failed, 0.0

    agreement = detail.get("agreement")
    detail["resolution"] = {
        "by_instructor_id": instructor_id,
        "at": datetime.now(UTC).isoformat(),
        "type": resolution,
        "reason": reason.strip(),
        "ai_majority_verdict": majority_verdict,
    }
    result.outcome = new_outcome
    result.score = new_score
    result.detail = detail

    session.add(
        AuditLog(
            event_type="escalation_resolved",
            check_run_id=check_run_id,
            agreement_score=Decimal(str(agreement)) if agreement is not None else None,
            payload={
                "check_result_id": check_result_id,
                "criterion_id": result.criterion_id,
                "instructor_id": instructor_id,
                "resolution": resolution,
                "reason": reason.strip(),
                "ai_majority_verdict": majority_verdict,
                "new_outcome": new_outcome.value,
                "new_score": new_score,
            },
        )
    )
    await session.commit()
    await session.refresh(result)
    return result
