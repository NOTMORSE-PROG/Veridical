"""Criterion router (F3.1): decides, for every confirmed criterion in a
rubric, whether it runs through the deterministic rule engine (V-016) or
AI grading (V-017) — and guarantees the post-run invariant that every
criterion ends up with exactly one check_result, never silently skipped
(charter rule 1).

Three outcomes per criterion:
- semantic (by type, or by structural-with-no-matching-rule — "honest
  degradation", logged so it's visible, not silently swapped)
- structural (type is structural AND a registry rule matches)
- unroutable (defensive only: the criterion's type isn't a recognized
  value at all) -> persisted directly as `not_applicable`, since nothing
  downstream can execute it either.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.rules import CriterionLike, find_matching_rule
from app.messages import CRITERION_TYPE_UNRECOGNIZED, STRUCTURAL_RULE_UNIMPLEMENTED
from app.models.audit import AuditLog
from app.models.enums import CheckKind, CriterionType, ResultOutcome
from app.models.run import CheckResult


@dataclass(frozen=True)
class RouteDecision:
    """One criterion's routing outcome. `kind`/`rule_id` tell the executor
    (V-016/V-017) what to run; `unroutable=True` means nothing runs — the
    router already recorded the terminal `not_applicable` result itself."""

    criterion_id: int
    kind: CheckKind
    rule_id: str | None
    degraded: bool
    note: str | None
    unroutable: bool = False


def route_criterion(criterion: CriterionLike, *, criterion_id: int, raw_type: str) -> RouteDecision:
    """Pure routing decision for one criterion. `raw_type` is passed
    separately (rather than trusting an already-validated enum) so the
    defensive unroutable path is actually exercisable — the ORM/pydantic
    layers normally guarantee `raw_type` is a valid CriterionType, but the
    router does not assume that guarantee holds forever (e.g. a future
    migration or a hand-edited row)."""
    if raw_type == CriterionType.semantic.value:
        return RouteDecision(
            criterion_id=criterion_id,
            kind=CheckKind.semantic,
            rule_id=None,
            degraded=False,
            note=None,
        )
    if raw_type == CriterionType.structural.value:
        rule = find_matching_rule(criterion)
        if rule is not None:
            return RouteDecision(
                criterion_id=criterion_id,
                kind=CheckKind.structural,
                rule_id=rule.rule_id,
                degraded=False,
                note=None,
            )
        return RouteDecision(
            criterion_id=criterion_id,
            kind=CheckKind.semantic,
            rule_id=None,
            degraded=True,
            note=STRUCTURAL_RULE_UNIMPLEMENTED,
        )
    return RouteDecision(
        criterion_id=criterion_id,
        kind=CheckKind.semantic,
        rule_id=None,
        degraded=True,
        note=CRITERION_TYPE_UNRECOGNIZED,
        unroutable=True,
    )


def route_criteria(criteria: list) -> list[RouteDecision]:
    """Routes every criterion; always returns exactly one decision per
    input criterion (the coverage invariant, provable without a DB)."""
    return [
        route_criterion(criterion, criterion_id=criterion.id, raw_type=str(criterion.type))
        for criterion in criteria
    ]


async def apply_routing(
    session: AsyncSession, check_run_id: int, decisions: list[RouteDecision]
) -> list[RouteDecision]:
    """Persists the routing pass: one audit_log row recording every
    decision (AC: "routing decisions visible in audit_log"), plus a
    terminal `not_applicable` check_result for any unroutable criterion
    (AC: "never dropped"). Routable decisions are returned unchanged for
    the caller (V-018 orchestration) to execute and record.
    """
    session.add(
        AuditLog(
            event_type="criterion_routing",
            check_run_id=check_run_id,
            payload={
                "decisions": [
                    {
                        "criterion_id": d.criterion_id,
                        "kind": d.kind.value,
                        "rule_id": d.rule_id,
                        "degraded": d.degraded,
                        "note": d.note,
                        "unroutable": d.unroutable,
                    }
                    for d in decisions
                ]
            },
        )
    )
    for decision in decisions:
        if decision.unroutable:
            session.add(
                CheckResult(
                    check_run_id=check_run_id,
                    criterion_id=decision.criterion_id,
                    kind=decision.kind,
                    outcome=ResultOutcome.not_applicable,
                    detail={"reason": decision.note},
                )
            )
    await session.commit()
    return decisions
