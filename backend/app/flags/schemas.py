"""Flag evidence/annotation/override HTTP contract (F8.2/F8.4, screen 4i)."""

from pydantic import BaseModel, Field

from app.report.schemas import ReportOut


class FlagOut(BaseModel):
    id: int
    check_result_id: int
    check_run_id: int
    manuscript_group_label: str
    check_kind: str
    # None for integrity checks (F4-F7) — they aren't tied to a rubric
    # criterion (check_result.criterion_id is nullable, ENGINEERING §2).
    criterion_text: str | None
    severity: str
    # Agreement score from self-consistency voting (D-006), when the
    # underlying check used it — None for deterministic/rule-based checks.
    confidence: float | None
    evidence_excerpt: str
    page_anchor: str
    annotation: str | None
    overridden: bool
    override_reason: str | None
    # What the check itself concluded, straight from check_result.detail
    # — shown alongside the override so both are visible (ticket AC:
    # "AI said X · instructor overrode to Y").
    ai_verdict_summary: str | None
    ai_reasoning: str | None


class AnnotateFlagIn(BaseModel):
    annotation: str = Field(min_length=1)


class OverrideFlagIn(BaseModel):
    reason: str = Field(min_length=1)


class OverrideFlagOut(FlagOut):
    # The recomputed report state (ticket AC: "recomputes score/status
    # immediately") — same pattern as V-023's `ResolveEscalationOut`.
    report: ReportOut
