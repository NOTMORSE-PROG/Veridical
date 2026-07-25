"""Aggregation & scoring engine (F8.1, ENGINEERING.md §5): a PURE function
of (results, flags, config) -> (score, status) — no DB, no LLM, no
network, so it is trivially unit-testable against hand-computed examples
(ticket AC) and reusable both when a run finishes (persisting the
composite) and whenever the report is re-rendered (explainability).

Escalated/not_applicable/unverifiable/api_down/quota_exhausted criteria
are EXCLUDED from the weighted average entirely (never silently treated
as a pass or a fail) — they are the instructor's to resolve, not the
system's to guess (charter rule 1). If nothing is left to average (every
criterion excluded, or the remaining weight sums to zero), the result is
an honest `needs_review` with NO score, never a fabricated number
(charter rule 9).
"""

from dataclasses import dataclass
from typing import Any

from app.config import Settings, get_settings
from app.models.enums import FlagSeverity, ReadinessStatus, ResultOutcome

# Only these two outcomes represent an actual GRADE (pass/fail with a
# 0-100 `score`) — every other outcome is a state about the CHECK itself,
# never something to average into a number.
_DECIDABLE_OUTCOMES = frozenset({ResultOutcome.passed, ResultOutcome.failed})


@dataclass(frozen=True)
class ScorableResult:
    """One rubric criterion's check_result, shaped for scoring only —
    callers (V-018's aggregation step, V-020's report endpoint) project
    their ORM rows into this, keeping this module DB-free."""

    criterion_id: int
    weight: float
    outcome: ResultOutcome
    score: float | None  # 0-100; None for any non-decidable outcome


@dataclass(frozen=True)
class ScorableFlag:
    severity: FlagSeverity
    overridden: bool


@dataclass(frozen=True)
class CriterionContribution:
    """Per-criterion line of the explainability breakdown (ticket AC)."""

    criterion_id: int
    weight: float
    outcome: ResultOutcome
    score: float | None
    counted: bool
    weighted_contribution: float | None  # weight * score / 100, only when counted


@dataclass(frozen=True)
class ScoringResult:
    composite_score: float | None
    status: ReadinessStatus
    reason: str | None  # set only for the needs_review short-circuit
    thresholds: dict[str, float]
    flag_deduction: float
    unresolved_high_flag_count: int
    contributions: list[CriterionContribution]


def _contribution(result: ScorableResult) -> CriterionContribution:
    counted = result.outcome in _DECIDABLE_OUTCOMES and result.score is not None
    weighted = (result.weight * result.score / 100.0) if counted else None
    return CriterionContribution(
        criterion_id=result.criterion_id,
        weight=result.weight,
        outcome=result.outcome,
        score=result.score,
        counted=counted,
        weighted_contribution=weighted,
    )


def _flag_deduction(flags: list[ScorableFlag], settings: Settings) -> tuple[float, int]:
    per_severity = {
        FlagSeverity.high: settings.scoring_flag_deduction_high,
        FlagSeverity.med: settings.scoring_flag_deduction_med,
        FlagSeverity.low: settings.scoring_flag_deduction_low,
    }
    unresolved = [f for f in flags if not f.overridden]
    total = sum(per_severity[f.severity] for f in unresolved)
    capped = min(total, settings.scoring_flag_deduction_cap)
    high_count = sum(1 for f in unresolved if f.severity == FlagSeverity.high)
    return capped, high_count


def score_check_run(
    results: list[ScorableResult],
    flags: list[ScorableFlag],
    settings: Settings | None = None,
) -> ScoringResult:
    settings = settings or get_settings()
    thresholds = {
        "ready_min_score": settings.scoring_ready_min_score,
        "not_ready_max_score": settings.scoring_not_ready_max_score,
    }
    contributions = [_contribution(r) for r in results]
    deduction, high_flag_count = _flag_deduction(flags, settings)

    counted = [c for c in contributions if c.counted]
    weight_sum = sum(c.weight for c in counted)

    if weight_sum <= 0:
        return ScoringResult(
            composite_score=None,
            status=ReadinessStatus.needs_review,
            reason=(
                "No criteria could be auto-scored (all escalated/not-applicable, or the "
                "decidable rubric weight is zero) — this run needs manual review."
            ),
            thresholds=thresholds,
            flag_deduction=deduction,
            unresolved_high_flag_count=high_flag_count,
            contributions=contributions,
        )

    raw_score = sum(c.weighted_contribution for c in counted) / weight_sum * 100.0
    composite = max(0.0, min(100.0, raw_score - deduction))

    if high_flag_count > 0:
        # ENGINEERING.md §5 verbatim: "Not Ready ... or any unresolved
        # high-severity flag" — a high flag excludes Ready by definition
        # (Ready requires "no high-severity flags") AND independently
        # satisfies the Not Ready condition, so it lands there directly,
        # never merely "Conditionally Ready" (a stricter, still-honest
        # reading of the ticket's own looser "forces status <= Cond.
        # Ready" wording — Not Ready is <= Conditionally Ready too).
        status = ReadinessStatus.not_ready
    elif composite >= thresholds["ready_min_score"]:
        status = ReadinessStatus.ready
    elif composite < thresholds["not_ready_max_score"]:
        status = ReadinessStatus.not_ready
    else:
        status = ReadinessStatus.conditionally_ready

    return ScoringResult(
        composite_score=composite,
        status=status,
        reason=None,
        thresholds=thresholds,
        flag_deduction=deduction,
        unresolved_high_flag_count=high_flag_count,
        contributions=contributions,
    )


def scoring_result_as_dict(result: ScoringResult) -> dict[str, Any]:
    """Explainability payload shape (ticket AC: "thresholds + per-criterion
    contribution breakdown"), plain-dict so it serializes straight into an
    API response or a JSONB column without a schema round-trip."""
    return {
        "composite_score": result.composite_score,
        "status": result.status.value,
        "reason": result.reason,
        "thresholds": result.thresholds,
        "flag_deduction": result.flag_deduction,
        "unresolved_high_flag_count": result.unresolved_high_flag_count,
        "contributions": [
            {
                "criterion_id": c.criterion_id,
                "weight": c.weight,
                "outcome": c.outcome.value,
                "score": c.score,
                "counted": c.counted,
                "weighted_contribution": c.weighted_contribution,
            }
            for c in result.contributions
        ],
    }
