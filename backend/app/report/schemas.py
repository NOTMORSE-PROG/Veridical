"""Readiness report HTTP contract (F8.1-F8.2, screen 4h)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.config import get_settings


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
    # BUG-051: the raw relative weight (report/scoring.py normalises it,
    # never requires a 100 total) -- kept for completeness, but NOT what
    # either renderer displays as of D-023: a raw number asserts a scale
    # it doesn't have. Display uses `weight_importance` instead.
    weight: float
    # D-023: this run's SHARE of the rubric's total weight, bucketed into
    # the same Low/Medium/High scale flag severity already uses
    # (`app/report/weight_importance.py`) -- computed once server-side so
    # both renderers (ResultsTable.tsx, export.py) render the SAME tag
    # instead of each re-deriving a number and drifting (Track D's
    # Critical 2, the exact duplication that let "999%" and a differently-
    # rounded weight ship in two places at once).
    weight_importance: str
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
    # BUG-052: whether the rubric this run graded against was confirmed
    # while `parse_status` was still `needs_review` (the parser's own
    # coverage gate wasn't satisfied at decomposition time). Confirming a
    # rubric is the instructor's resolution of that ambiguity (charter
    # rule 1, human-in-the-loop) -- but the fact that a warning EXISTED
    # must survive activation, not vanish the instant Confirm is clicked,
    # so an instructor or adviser reading the report later can see the
    # measuring instrument was flagged.
    rubric_needs_review: bool = False
    rubric_parse_issues: list[str] | None = None
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
    weight_importance: str
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
    # BUG-052: the adviser needs to know the measuring instrument was
    # flagged too, not just the instructor.
    rubric_needs_review: bool = False
    rubric_parse_issues: list[str] | None = None


class DecisionIn(BaseModel):
    decision: Literal["approved", "returned", "rejected"]
    note: str | None = None


class ReopenIn(BaseModel):
    # Always required (the decision note is only conditionally required,
    # BUG-095) -- undoing a decision is exactly the kind of action this
    # project's own audit-trail philosophy (V-024) says must explain
    # itself.
    reason: str = Field(min_length=1)

    # BUG-105: the identical defect BUG-096 fixed for ResolveEscalationIn
    # -- min_length=1 let a single character satisfy a field whose own
    # comment says it "must explain itself." Reuses
    # resolution_reason_min_length rather than inventing a second number.
    @field_validator("reason")
    @classmethod
    def _reason_meets_the_minimum(cls, value: str) -> str:
        stripped = value.strip()
        minimum = get_settings().resolution_reason_min_length
        if len(stripped) < minimum:
            raise ValueError(f"reason must be at least {minimum} characters")
        return stripped


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

    # BUG-096: `min_length=1` let a single character ("x") satisfy a field
    # labelled "Reason (required)" -- accepted, then published verbatim to
    # the report, the exported PDF, and the public share link. A real
    # floor (`resolution_reason_min_length`, config.py, not hardcoded here
    # per rule 7) is crude but honest about what "required" was supposed
    # to mean.
    @field_validator("reason")
    @classmethod
    def _reason_meets_the_minimum(cls, value: str) -> str:
        stripped = value.strip()
        minimum = get_settings().resolution_reason_min_length
        if len(stripped) < minimum:
            raise ValueError(f"reason must be at least {minimum} characters")
        return stripped


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


class FlagRegionOut(BaseModel):
    """V-065 AC1-4: what the manuscript viewer can actually do with one
    flag's anchor. `kind` is never fabricated past what
    `app.ingest.regions.recover_region` measured it can recover (real
    47-page manuscript, 2026-08-19): "bbox" only when a precise box was
    found on the page; "page_only" is an honest degraded state (the page
    is real, no box is), not an error."""

    flag_id: int
    kind: Literal[
        "page_bbox",
        "page_only",
        "reference_list",
        "reference_position",
        "section",
        "whole_document",
        "paragraph_only",
        "unavailable",
    ]
    page: int | None
    end_page: int | None
    bbox: tuple[float, float, float, float] | None
    all_bboxes: list[tuple[float, float, float, float]]
    paragraph: int | None
    index: int | None


class ManuscriptViewerOut(BaseModel):
    """V-065 AC1/7: what screen 4h's "open the manuscript" action loads.
    `source_format` decides which pane the frontend renders (PDF.js vs
    extracted text, decided 2026-08-19 — see V-065.md Q1) — never
    guessed client-side from the byte content."""

    manuscript_id: int
    original_filename: str | None
    source_format: Literal["pdf", "docx", "unknown"]
    available: bool
    # Populated only when `available` is False — the reason is always
    # stated, never a silent empty pane (ground rule 3 / AC7).
    unavailable_reason: str | None
    purged_at: datetime | None
    page_count: int | None
    regions: list[FlagRegionOut]


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
