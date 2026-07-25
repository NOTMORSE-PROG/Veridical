"""Confidence-based escalation gate (V-022/V-023, F3.5, D-006): the ONLY
place that decides whether a self-consistency vote (V-022) is trusted
enough to auto-score, or must go to the instructor instead (charter rule
1 — a low-confidence verdict is escalated, never guessed).

`gate_vote` is a pure function so the threshold decision is unit-testable
in isolation from any LLM call; `app.checks.consistency` calls it at
persist time. The escalated-panel listing + instructor resolution flow
(the consumer-facing half of F3.5) is V-023's own addition to this module.
"""

from app.config import Settings, get_settings
from app.models.enums import ResultOutcome

_SCORE_BY_VERDICT = {"pass": 100.0, "partial": 50.0, "fail": 0.0}
_OUTCOME_BY_VERDICT = {
    "pass": ResultOutcome.passed,
    "partial": ResultOutcome.passed,
    "fail": ResultOutcome.failed,
}


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
