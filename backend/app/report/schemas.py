"""Readiness report HTTP contract (F8.1-F8.2, screen 4h)."""

from pydantic import BaseModel


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
