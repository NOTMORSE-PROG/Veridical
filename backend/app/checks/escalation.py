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

from app.checks.levels import is_levelled, match_level_by_ordinal, outcome_and_score
from app.config import Settings, get_settings
from app.errors import ConflictError, NotFoundError
from app.models.audit import AuditLog
from app.models.enums import ResultOutcome
from app.models.rubric import Criterion
from app.models.run import CheckResult

# Instructor's resolution choices: accept whatever the AI's own majority
# vote was, override to a specific verdict outright, or (V-068 AC2/BUG-096
# DECIDED 2026-08-16) say plainly that a real decision needs the document
# itself rather than guessing.
RESOLUTION_ACCEPT_MAJORITY = "accept_majority"
RESOLUTION_MARK_PASS = "mark_pass"
RESOLUTION_MARK_FAIL = "mark_fail"
RESOLUTION_NEEDS_DOCUMENT = "needs_document"
# V-069 AC4: "on a 1-4 rubric they choose a level, not Pass/Fail" -- the
# instructor's own resolution vocabulary must match the rubric's own
# scale, the identical principle the ticket's Responsibilities section
# names for `escalation.py:28-32`'s pre-existing pass/fail-only gap.
RESOLUTION_MARK_LEVEL = "mark_level"
_VALID_RESOLUTIONS = {
    RESOLUTION_ACCEPT_MAJORITY,
    RESOLUTION_MARK_PASS,
    RESOLUTION_MARK_FAIL,
    RESOLUTION_NEEDS_DOCUMENT,
    RESOLUTION_MARK_LEVEL,
}

# Outcomes that mean "a human still has to decide this one". They are NOT
# interchangeable — see `list_escalated` — but they share a workflow: shown
# in the review panel, excluded from the composite, resolvable by the
# instructor (V-050).
NEEDS_REVIEW_OUTCOMES = (
    ResultOutcome.escalated,
    ResultOutcome.quota_exhausted,
    ResultOutcome.api_down,
)

# Why an item is in the panel, as a stable code the UI can label. The
# instructor must be able to tell "the AI looked and hesitated" from "the AI
# never looked" — they justify very different amounts of trust.
REVIEW_REASON_LOW_CONFIDENCE = "low_confidence"
REVIEW_REASON_NOT_GRADED = "not_graded"
# BUG-045: a THIRD reason, distinct from both of the above. This is not "the
# AI hesitated" (`low_confidence`) — the vote may show perfect agreement —
# and it is not "the AI never ran" (`not_graded`). It is "the AI agreed, and
# that agreement cannot be trusted," which needs its own label so a UI never
# renders a bare "Agreement N/N" line as if it meant confidence here
# (backend-critic review, 2026-08-24, finding F1).
REVIEW_REASON_INJECTION_SUSPECTED = "injection_suspected"


def review_reason_for(outcome: ResultOutcome, detail: dict[str, Any] | None = None) -> str:
    if detail and detail.get("injection_suspected"):
        return REVIEW_REASON_INJECTION_SUSPECTED
    if outcome == ResultOutcome.escalated:
        return REVIEW_REASON_LOW_CONFIDENCE
    return REVIEW_REASON_NOT_GRADED


def gate_vote(
    majority_verdict: str | None,
    agreement: float,
    settings: Settings | None = None,
    *,
    criterion: Any = None,
) -> tuple[ResultOutcome, float | None]:
    """Turns a self-consistency vote into a persistable (outcome, score).

    No majority at all (a genuine 3-way pass/partial/fail split) always
    escalates — that's a structural fact about the vote, not a policy
    choice, so it isn't gated by the threshold (ticket edge case: "never
    majority-by-technicality"). A real majority still escalates if its
    agreement falls below the configured threshold (default 1.0 — ANY
    disagreement escalates until golden-set evidence justifies loosening
    it, D-012).

    `criterion` (V-069, keyword-only, defaults to `None`): when given and
    levelled, the verdict is matched against THAT criterion's own level
    names instead of the pass/partial/fail vocabulary
    (`app.checks.levels.outcome_and_score` — the single source of truth
    both this function and `semantic.py`'s single-pass path now share).
    Every existing call site that omits it keeps the exact old behavior,
    including for an unmapped verdict string, which is now an honest
    escalation instead of a `KeyError` — unreachable under the old
    `Literal["pass","partial","fail"]`-validated schema, so not a
    regression against anything that could previously happen.
    """
    settings = settings or get_settings()
    if majority_verdict is None:
        return ResultOutcome.escalated, None
    if agreement < settings.escalation_agreement_threshold:
        return ResultOutcome.escalated, None
    outcome, score, _level = outcome_and_score(criterion, majority_verdict)
    return outcome, score


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
    # "low_confidence" (AI graded, wasn't sure) vs "not_graded" (AI never
    # ran — quota spent or API down). Same panel, different amount of
    # evidence behind the row.
    review_reason: str
    # V-068 Q2: quotes the model actually returned but that failed
    # containment verification — shown separately from (never inside) a
    # verified `evidence` list, since verification is what produces a real
    # anchor (no partial anchor exists to report for these).
    unverified_evidence: list[str] | None = None
    # BUG-045: True when `app.checks.injection` matched language addressed
    # at a grader/system/AI in this criterion's batch context. `snippet` is
    # the short, bounded excerpt that matched — the actual evidence an
    # instructor can check in 10 seconds (judgment §1), not just a claim.
    injection_suspected: bool = False
    injection_matched_snippet: str | None = None
    # V-069 AC4: the criterion's own scale, so the resolve panel can render
    # a level picker instead of Pass/Fail — `None`/empty for an ordinary
    # pass/fail criterion, unchanged from before this ticket.
    levels: list[dict[str, Any]] | None = None


async def list_escalated(session: AsyncSession, check_run_id: int) -> list[EscalatedItem]:
    """Screen 4h's "needs your review" panel: every semantic criterion this
    run could not decide on its own, oldest first so the panel order is
    stable across reloads.

    TWO distinct reasons land here and are kept distinguishable (V-050) —
    conflating them would be exactly the dishonesty the taxonomy exists to
    prevent (TESTING §5):
    - `escalated`: the AI graded it and was not confident enough (D-006).
    - `quota_exhausted` / `api_down`: the AI never graded it at all. Nothing
      was judged; the item is here so the run stays USABLE without pretending
      an opinion exists.
    Both are the instructor's to resolve, and neither ever scores itself.
    """
    rows = (
        await session.execute(
            select(CheckResult, Criterion)
            .join(Criterion, Criterion.id == CheckResult.criterion_id)
            .where(
                CheckResult.check_run_id == check_run_id,
                CheckResult.outcome.in_(NEEDS_REVIEW_OUTCOMES),
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
                review_reason=review_reason_for(result.outcome, detail),
                unverified_evidence=detail.get("unverified_evidence"),
                injection_suspected=bool(detail.get("injection_suspected")),
                injection_matched_snippet=detail.get("injection_matched_snippet"),
                levels=criterion.levels,
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
    settings: Settings | None = None,
    *,
    # V-069 AC4: the level ORDINAL the instructor picked, required (and
    # only meaningful) when `resolution == RESOLUTION_MARK_LEVEL`.
    level: int | None = None,
) -> CheckResult:
    """One instructor decision on one escalated criterion (ticket AC): the
    ONLY way an escalated result ever becomes a score contribution again —
    never automatic (charter rule 1). The original AI vote is preserved
    (never overwritten) so the report can show "AI said X · instructor
    resolved to Y — reason" side by side; only `outcome`/`score` change to
    reflect the human decision, which is what `aggregate_and_score`
    actually counts.
    """
    # Reason presence/length is primarily enforced by the router's request
    # schema (`ResolveEscalationIn`'s `field_validator`, CODING.md §1:
    # routers validate); this is a defensive second check for any other
    # caller of this service function. `backend-critic` finding (BUG-096
    # review): this used to check presence only, so a caller that bypassed
    # the schema would still let a one-character reason through -- the
    # exact defect BUG-096 fixed at the schema layer, reopened one layer
    # down.
    settings = settings or get_settings()
    if not reason or len(reason.strip()) < settings.resolution_reason_min_length:
        raise ConflictError(
            f"A reason of at least {settings.resolution_reason_min_length} characters is "
            "required to resolve an escalated item."
        )
    if resolution not in _VALID_RESOLUTIONS:
        raise ConflictError(f"Unknown resolution {resolution!r}.")

    result = await session.get(CheckResult, check_result_id)
    if result is None or result.check_run_id != check_run_id:
        raise NotFoundError(f"No check result {check_result_id} on check run {check_run_id}.")
    # BUG-073 follow-up: an F4/F5 integrity check (criterion_id always
    # None) can now also carry a needs-review outcome (a partially-
    # executed run, quota_exhausted/api_down) -- this endpoint's whole
    # resolution vocabulary (accept_majority/mark_pass/mark_fail/
    # needs_document, `_OUTCOME_BY_VERDICT`/`_SCORE_BY_VERDICT`) is
    # rubric-criterion grading semantics and doesn't mean anything for an
    # integrity check. `list_escalated`'s own INNER JOIN to Criterion
    # already keeps these off the review panel; this guard closes the
    # same gap for a caller that reaches this endpoint directly with an
    # integrity check's id instead of going through the panel.
    if result.criterion_id is None:
        raise ConflictError(
            "This isn't a rubric criterion awaiting review, so there's nothing to resolve here."
        )
    if result.outcome not in NEEDS_REVIEW_OUTCOMES:
        raise ConflictError("This criterion isn't awaiting review, so there's nothing to resolve.")

    # V-069 AC4: fetched unconditionally (cheap, single extra PK lookup) —
    # needed by accept_majority (a levelled criterion's own vote uses its
    # level vocabulary too), mark_level, and the pass/fail-on-a-levelled-
    # criterion guard below, so a conditional fetch would just duplicate
    # this same call three ways.
    criterion = await session.get(Criterion, result.criterion_id)

    detail = dict(result.detail or {})
    majority_verdict = detail.get("verdict")
    resolved_level: dict[str, Any] | None = None

    if resolution in (RESOLUTION_MARK_PASS, RESOLUTION_MARK_FAIL) and is_levelled(criterion):
        raise ConflictError(
            "This criterion has its own named performance levels. Choose mark_level "
            "with a level instead of Pass/Fail."
        )
    if resolution == RESOLUTION_MARK_LEVEL and not is_levelled(criterion):
        raise ConflictError("This criterion doesn't have named performance levels.")

    if resolution == RESOLUTION_ACCEPT_MAJORITY:
        if majority_verdict is None:
            # Covers both "the vote genuinely tied" and "the AI never ran at
            # all" (V-050). Either way there is no AI opinion to accept, and
            # inventing one would be the exact failure this system exists to
            # prevent.
            raise ConflictError(
                "The AI never reached a majority verdict on this criterion, "
                "choose mark_pass or mark_fail instead."
            )
        new_outcome, new_score, level_match = outcome_and_score(criterion, majority_verdict)
        if new_outcome == ResultOutcome.escalated:
            raise ConflictError(
                "The AI's own vote used a verdict that no longer matches this criterion's "
                "scale; choose mark_pass, mark_fail, or mark_level instead."
            )
        if level_match is not None:
            resolved_level = level_match.as_detail()
    elif resolution == RESOLUTION_MARK_PASS:
        new_outcome, new_score = ResultOutcome.passed, 100.0
    elif resolution == RESOLUTION_MARK_FAIL:
        new_outcome, new_score = ResultOutcome.failed, 0.0
    elif resolution == RESOLUTION_MARK_LEVEL:
        level_match = match_level_by_ordinal(criterion, level)
        if level_match is None:
            raise ConflictError(f"Level {level!r} isn't one of this criterion's own levels.")
        if not level_match.max_points:
            # `backend-critic` finding, live-reproduced: a degenerate all-
            # zero-points scale (now blocked at the source by
            # `ParsedLevel`'s own validator, guarded here too for any
            # criterion that predates that check) used to raise a raw,
            # unhandled `ZeroDivisionError` -- an unexplained 500 on the
            # highest-stakes screen in the product, instead of an honest,
            # catchable error.
            raise ConflictError(
                "This criterion's own scale has no rung worth more than 0 points, so it "
                "can't produce a real score. This is a problem with the rubric itself."
            )
        new_outcome = ResultOutcome.passed
        new_score = level_match.points / level_match.max_points * 100.0
        resolved_level = level_match.as_detail()
    else:  # RESOLUTION_NEEDS_DOCUMENT — DECIDED 2026-08-16: excludes from
        # the composite exactly like `not_applicable` already behaves
        # (never a 0, never a pass), and does NOT block the final decision
        # (`decide_report`'s pending count only watches NEEDS_REVIEW_
        # OUTCOMES, which `not_applicable` was never a member of).
        new_outcome, new_score = ResultOutcome.not_applicable, None

    if resolved_level is not None:
        detail["level"] = resolved_level
    elif "level" in detail:
        # Overriding a levelled criterion's own AI-suggested level with a
        # plain mark_pass/mark_fail is blocked above, so this only clears a
        # STALE level (e.g. a resolution changed via a second call) —
        # defensive, not a reachable path today.
        del detail["level"]

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
