"""Readiness report HTTP contract (F8.1-F8.2, screen 4h)."""

from datetime import datetime
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
    # BUG-022: group_label defaults to "Ungrouped" and can't distinguish
    # two manuscripts alone; None for rows ingested before this column
    # existed.
    manuscript_original_filename: str | None
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
    # BUG-049: "fake" (fixture data, no real Gemini call), "real", or
    # "unknown" (a run that predates this field, migration 0024's
    # backfill) — shown wherever this report's verdict is shown so
    # fixture-derived flags/scores can never be mistaken for real
    # findings about the manuscript.
    llm_mode: str
    results: list[CriterionResultOut]
    # V-038 (F8.5) — the terminal gate. `decision` is None until the
    # instructor decides; once set, the report is frozen (blocked from a
    # new decision) until an explicit, reasoned reopen.
    decision: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    # Gates the decide action client-side (the server re-checks
    # authoritatively regardless — this is for the UI to explain BEFORE a
    # blocked attempt, per AC1: "blocked w/ count + link to panel").
    pending_review_count: int = 0
    # Edge case: a decision made against a rubric version that is no
    # longer the active one for its family (a newer version has since been
    # confirmed) — surfaces as a warning banner, never silently hidden.
    rubric_is_current: bool = True
    # V-041 — the version-comparison line: the same manuscript's most
    # recent OTHER done+reported run, if one exists (e.g. this run is a
    # re-check against a newer rubric version). None when this is the
    # manuscript's first reported run — never a fabricated comparison.
    previous_status: str | None = None
    previous_composite_score: float | None = None


class PublicCriterionResultOut(BaseModel):
    """BUG-044 fix: the public, unauthenticated adviser view's (screen 4l)
    per-criterion row — an explicit, minimal projection, deliberately NOT
    `CriterionResultOut`, so a field added to the instructor-facing model
    is never auto-published to an anonymous reader. Excludes `resolution`
    specifically: it carries the instructor's own private reasoning for
    overriding an AI verdict (e.g. "I'm passing this because the student
    has had a rough term"), which `SharedReportOut`'s own prior docstring
    claimed was already excluded — it wasn't, because that version was
    typed as `ReportOut` itself and inherited every field `ReportOut` ever
    grows."""

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


class PublicReportOut(BaseModel):
    """BUG-044 fix: the public, unauthenticated adviser view's report
    payload — deliberately NOT `ReportOut`. Excludes `previous_status`/
    `previous_composite_score` (a DIFFERENT, never-shared check run's own
    outcome — V-041's comparison line was never meant for this audience)
    and `pending_review_count` (the frontend derives an equivalent count
    from `results` itself; the field doesn't need a public value at all).
    `decision_note` stays: V-040's own `ShareModal.tsx`/`DecisionModal.tsx`
    copy already discloses it specifically to the instructor before they
    share or write one — the one decision-adjacent field this audience
    legitimately has informed consent to see."""

    check_run_id: int
    manuscript_group_label: str
    manuscript_original_filename: str | None
    rubric_title: str
    status: str
    composite_score: float | None
    thresholds: dict[str, float]
    reason: str | None
    flag_deduction: float
    unresolved_high_flag_count: int
    # BUG-049: the public adviser view has NO other context at all --
    # this is the one audience that matters most for this disclosure.
    llm_mode: str
    results: list[PublicCriterionResultOut]
    decision: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    rubric_is_current: bool = True


class DecisionIn(BaseModel):
    decision: Literal["approved", "returned", "rejected"]
    note: str | None = None


class ReopenIn(BaseModel):
    # Required, unlike the decision note — a decision can be made with no
    # elaboration, but undoing one is exactly the kind of action this
    # project's own audit-trail philosophy (V-024) says must explain
    # itself.
    reason: str = Field(min_length=1)


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


class FlagSummaryOut(BaseModel):
    """BUG-033: the report's own flags list (F4-F7) — deliberately a
    smaller field set than `flags.schemas.FlagOut`. A summary row's job
    is "enough to decide whether to click," not "enough to skip
    clicking" — Zhang/Liao/Bellamy (FAccT 2020) and Bansal et al. (CHI
    2021), already cited in this project's agent research (RESEARCH.md
    §11), found more explanatory text around a verdict does not improve
    calibration and can increase blind acceptance. `confidence`,
    `override_reason`, `ai_verdict_summary`, `ai_reasoning`, and
    `annotation` stay detail-page-only (`/flags/{id}`, `ui-designer`
    spec, 2026-08-13)."""

    id: int
    check_kind: str
    severity: str
    criterion_text: str | None
    evidence_excerpt: str
    page_anchor: str
    overridden: bool


class ReportExportData(BaseModel):
    """V-039: everything the PDF export needs, gathered once. `flags`
    mirrors the report's own flags panel (BUG-033) exactly -- the export
    is a print-adapted view of the same instructor-facing data, never a
    second source of truth. `archive_size_n` is None when the F7 check
    never ran (no embeddable content extracted) -- an honest gap, not a
    fabricated zero."""

    report: ReportOut
    flags: list[FlagSummaryOut]
    archive_size_n: int | None
