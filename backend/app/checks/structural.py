"""Structural check engine entry point (F3.2): executes a routed structural
criterion's rule and persists its check_result. This is the piece V-018's
orchestrator calls for every `RouteDecision` where `kind == structural`.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.router import RouteDecision
from app.checks.rules import CriterionLike, RuleContext, RuleOutcome, get_rule
from app.models.enums import CheckKind, ResultOutcome
from app.models.run import CheckResult

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
    outcome = (
        spec.run(criterion, ctx) if spec is not None else _missing_rule_outcome(decision.rule_id)
    )
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
