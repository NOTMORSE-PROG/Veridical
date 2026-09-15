"""Structural check engine entry point (F3.2): executes a routed structural
criterion's rule and persists its check_result. This is the piece V-018's
orchestrator calls for every `RouteDecision` where `kind == structural`.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.router import RouteDecision
from app.checks.rules import CriterionLike, RuleContext, RuleOutcome, get_rule
from app.models.enums import CheckKind, ResultOutcome
from app.models.run import CheckResult

logger = logging.getLogger(__name__)

# Structural results are binary by nature (a rule either holds or it
# doesn't) — this is the ONE place that mapping to a numeric score lives,
# so V-019's aggregation never has to special-case "how do I score a
# structural pass" per rule.
_OUTCOME_SCORE = {
    ResultOutcome.passed: 100.0,
    ResultOutcome.failed: 0.0,
}


def _missing_rule_outcome(rule_id: str) -> RuleOutcome:
    """Defensive only: the router named a `rule_id` that isn't registered
    (e.g. a rule was removed after routing ran). Never crashes the run —
    degrades to an honest not_applicable, same charter-rule-1 guarantee
    V-015 already gives unroutable criteria."""
    return RuleOutcome(
        outcome=ResultOutcome.not_applicable,
        anchor="document",
        detail={"reason": f"Rule '{rule_id}' is not registered."},
    )


def _raising_rule_outcome(rule_id: str, exc: Exception) -> RuleOutcome:
    """BUG-161: the missing-rule guard above defended against a rule that
    isn't there; nothing defended against a rule that IS there but raises
    (bbox math, reference parsing, table shape checks -- arbitrary student
    PDF content is exactly what produces the degenerate input that trips
    these). A raising rule used to propagate all the way to `machine.py`'s
    catch-all and fail the WHOLE run, including every criterion already
    passed and every integrity check not yet reached. Same charter-rule-1
    treatment as `_missing_rule_outcome`, extended rather than a second
    convention invented: this ONE criterion degrades to an honest
    not_applicable while the run keeps going.

    `reason` is the one `detail` key `report/service.py` projects to the
    instructor-facing screen (`CriterionResultOut.reason`) -- it must stay
    in the same honest, non-technical register `_missing_rule_outcome`
    already uses above, never a raw exception string (the same charter-9
    risk `machine.py`'s semantic-stage degradation already documents:
    "a raw exception string embedded in a note could say anything"). The
    exception type is logged server-side only, via `logger.exception`,
    which captures the full traceback for a developer to investigate --
    discoverable without being instructor-facing."""
    logger.exception("structural rule %r raised while grading criterion", rule_id)
    return RuleOutcome(
        outcome=ResultOutcome.not_applicable,
        anchor="document",
        detail={
            "reason": f"Rule '{rule_id}' could not be evaluated automatically for this manuscript."
        },
    )


async def run_structural_check(
    session: AsyncSession,
    check_run_id: int,
    criterion: CriterionLike,
    criterion_id: int,
    decision: RouteDecision,
    ctx: RuleContext,
) -> CheckResult:
    assert decision.kind == CheckKind.structural and decision.rule_id is not None
    spec = get_rule(decision.rule_id)
    if spec is None:
        outcome = _missing_rule_outcome(decision.rule_id)
    else:
        try:
            outcome = spec.run(criterion, ctx)
        except Exception as exc:
            outcome = _raising_rule_outcome(decision.rule_id, exc)
    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=criterion_id,
        kind=CheckKind.structural,
        outcome=outcome.outcome,
        score=_OUTCOME_SCORE.get(outcome.outcome),
        detail={"rule_id": decision.rule_id, "anchor": outcome.anchor, **outcome.detail},
    )
    session.add(result)
    await session.commit()
    return result
