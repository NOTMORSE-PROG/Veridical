"""Readiness report HTTP contract (F8.1-F8.2, screen 4h)."""

from typing import Literal

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    quote: str
    anchor: str


class ResolutionOut(BaseModel):
    """Present only when an instructor resolved this criterion out of the
    escalation panel (`resolve_escalation`'s own docstring: "AI said X,
    instructor resolved to Y, reason" side by side) — surfacing this was
    previously dropped between the persisted `detail` JSON and the API
    response, so a human decision rendered on screen 4h as an ordinary
    AI-graded row with the AI's original (superseded) failure text and
    no trace of the instructor's own reason (V-055 review)."""

    type: str
    reason: str
    ai_majority_verdict: str | None


class CriterionResultOut(BaseModel):
    """One row of screen 4h's results table. `basis`/`anchor`/`reasoning`/
    `reason`/`evidence` all come straight from `check_result.detail` — the
    shape varies by kind (structural vs semantic vs escalated), so every
    field is optional and the UI shows only what's actually present
    (never a fabricated placeholder)."""

    criterion_id: int
    text: str
    type: str
    weight: float
    kind: str
    outcome: str
    score: float | None
    basis: str | None
    anchor: str | None
    reasoning: str | None
    reason: str | None
    evidence: list[EvidenceItem]
    resolution: ResolutionOut | None


class ReportOut(BaseModel):
    check_run_id: int
    manuscript_group_label: str
    rubric_title: str
    status: str
    composite_score: float | None
    thresholds: dict[str, float]
    reason: str | None
    # Already computed by score_check_run (scoring.py) but previously
    # dropped before reaching the API — without these the frontend could
    # only hedge ("Not Ready because the score is X% OR a flag exists")
    # instead of stating the actual determining factor (V-055 4h review).
    flag_deduction: float
    unresolved_high_flag_count: int
    results: list[CriterionResultOut]


class EscalatedItemOut(BaseModel):
    """One row of the "needs your review" panel (V-023, rendered FIRST on
    screen 4h — never a silent score contribution)."""

    check_result_id: int
    criterion_id: int
    criterion_text: str
    weight: float
    agreement: float | None
    votes: list[str | None]
    ai_majority_verdict: str | None
    reason: str | None
    # "low_confidence" = the AI graded it and hesitated; "not_graded" = the
    # AI never ran (quota spent / API down). The instructor must be able to
    # tell these apart (V-050) — they carry very different evidence.
    review_reason: str = "low_confidence"


class ResolveEscalationIn(BaseModel):
    resolution: Literal["accept_majority", "mark_pass", "mark_fail"]
    # Router validates presence (CODING.md §1) so the service's own check
    # is defense-in-depth, not the primary gate.
    reason: str = Field(min_length=1)


class ResolveEscalationOut(BaseModel):
    check_result_id: int
    outcome: str
    score: float | None
    # The recomputed report state (AC: "resolution updates score + status
    # live") — the frontend never has to guess whether a second fetch is
    # needed.
    report: ReportOut
