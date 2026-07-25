"""Readiness report HTTP contract (F8.1-F8.2, screen 4h)."""

from typing import Literal

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    quote: str
    anchor: str


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


class ReportOut(BaseModel):
    check_run_id: int
    manuscript_group_label: str
    rubric_title: str
    status: str
    composite_score: float | None
    thresholds: dict[str, float]
    reason: str | None
    results: list[CriterionResultOut]


class EscalatedItemOut(BaseModel):
    """One row of the "AI wasn't sure — review these" panel (V-023,
    rendered FIRST on screen 4h — never a silent score contribution)."""

    check_result_id: int
    criterion_id: int
    criterion_text: str
    weight: float
    agreement: float | None
    votes: list[str | None]
    ai_majority_verdict: str | None
    reason: str | None


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
