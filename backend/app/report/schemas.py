"""Readiness report HTTP contract (F8.1-F8.2, screen 4h)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.config import get_settings


class EvidenceItem(BaseModel):
    quote: str
    anchor: str


class CriterionLevelOut(BaseModel):
    """V-069 AC2: a decided levelled criterion's own rung — the judgment
    the instructor reads (owner-approved treatment, 2026-08-25), not a
    percentage. `points`/`max_points` ride along for the RATING
    transcription, never shown as the primary verdict on their own."""

    name: str
    ordinal: int
    points: float
    max_points: float


class RubricLevelOut(BaseModel):
    """One rung of a criterion's OWN scale, as captured at decomposition
    (or hand-edit) time -- the resolve panel's level-picker data source
    (V-069 AC4), distinct from `CriterionLevelOut` (a DECIDED result)."""

    level: int
    name: str
    descriptor: str
    points: float


class LevelledRatingOut(BaseModel):
    """V-069 AC2: the rubric's own RATING formula, transcribed — see
    `app.report.rating`'s module docstring for why this is never merged
    into the composite score."""

    achieved_points: float
    max_points: float
    rating_percent: float
    n_decided: int
    n_levelled: int


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
    # V-069 AC2: present only for a levelled criterion's decided result —
    # the judgment the instructor reads for that row (owner-approved
    # treatment). `None` for every ordinary pass/fail criterion, unchanged.
    level: CriterionLevelOut | None = None


class IntegrityCheckStatusOut(BaseModel):
    """BUG-125: on-screen disclosure that an F4/F5 integrity check did not
    fully execute. BUG-073 (same day) made the underlying `CheckResult`
    honest about this (`outcome` is `unverifiable`/`api_down`/
    `quota_exhausted`, never a masked `passed`) -- nothing surfaced it on
    screen until this. A narrow, explicit projection of `CheckResult.detail`,
    never a passthrough: F7's own `detail` carries internal-only fields
    (`matched_group_label`, BUG-050/BUG-097) that must never reach an API
    response, and the same caution applies here even though F4/F5's detail
    doesn't currently hold anything that sensitive -- the projection stays
    explicit on principle, not because today's fields happen to be safe."""

    check_kind: str
    outcome: str
    n_skipped_quota: int
    n_skipped_api_down: int
    n_skipped_parse_failure: int


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
    # BUG-125: empty when every F4/F5 integrity check either fully ran or
    # never applies to this manuscript (`not_applicable`) -- never a
    # fabricated empty state, the same convention `rubric_parse_issues`
    # already follows.
    integrity_check_status: list[IntegrityCheckStatusOut] = []
    # V-069 AC2/AC3: `None` whenever no criterion in this rubric is
    # levelled — a pass/fail rubric's report gains no new visible field.
    levelled_rating: LevelledRatingOut | None = None


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
    # V-069 AC5: the adviser view carries the level too, same as the
    # instructor-facing report — a level name is what was decided, not
    # instructor-private reasoning (unlike `resolution`, deliberately
    # still excluded here).
    level: CriterionLevelOut | None = None
    # V-069 (`ux-critic` finding, live-reproduced): `resolution` is
    # deliberately excluded above (BUG-044, the reason text is private),
    # but that left this row's PROVENANCE label with no honest signal at
    # all — `sourceCaption()`/`_source_caption()` fell through to
    # "AI-graded" for a criterion an instructor actually resolved by hand,
    # showing the AI's own superseded evidence as if it were the final
    # word. `resolved` is the non-private half of `resolution` (a bare
    # fact, not the reasoning) — safe to publish, same boundary BUG-052
    # already drew for `rubric_needs_review`.
    resolved: bool = False


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
    levelled_rating: LevelledRatingOut | None = None


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
    # V-068 AC1/Q2: quotes the model actually returned but that failed
    # containment verification — the charter's 10-second verification bar
    # applied to the panel that most needed it and had none before. Never
    # merged into a verified `evidence` list (no real anchor exists for an
    # unverified quote); rendered under its own "could not verify" label.
    unverified_evidence: list[str] | None = None
    # BUG-045: True when the batch's document text matched a pattern for
    # language addressed at a grader/system/AI — `review_reason` is already
    # "injection_suspected" in that case, but the UI needs the actual
    # matched excerpt to let the instructor verify the flag directly rather
    # than trusting a bare claim (judgment §1's 10-second verification bar).
    injection_suspected: bool = False
    injection_matched_snippet: str | None = None
    # V-069 AC4: the criterion's own scale, so the panel can render a level
    # picker instead of Pass/Fail — `None` for an ordinary pass/fail
    # criterion, unchanged from before this ticket.
    levels: list[RubricLevelOut] | None = None


class ResolveEscalationIn(BaseModel):
    # "needs_document" (V-068 AC2, DECIDED 2026-08-16): a third option that
    # isn't a guess — excludes the criterion from the composite like
    # `not_applicable`, never blocks the decision.
    # "mark_level" (V-069 AC4): the resolution vocabulary for a levelled
    # criterion — requires `level` below.
    resolution: Literal["accept_majority", "mark_pass", "mark_fail", "needs_document", "mark_level"]
    # Router validates presence (CODING.md §1) so the service's own check
    # is defense-in-depth, not the primary gate.
    reason: str = Field(min_length=1)
    # V-069 AC4: required (and only meaningful) when resolution is
    # "mark_level" — the level's own ordinal, matched against the
    # criterion's own scale server-side (never trusted as a name/points
    # pair the client could get wrong).
    level: int | None = None

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
    # V-072 (F7.4): distinguishes a passage-level reuse flag from today's
    # whole-document/chapter-level ones (same `check_kind`,
    # `originality_reuse`) -- lets the passage-pair exploration panel
    # list exactly its own scored matches without inventing a second
    # request. False for every non-F7.4 flag, including every other
    # check kind.
    is_passage_level: bool = False
    # BUG-097 (presentation-only remedy, owner ruling 2026-08-24): mirrors
    # FlagOut's own field (app/flags/schemas.py) -- see that class's
    # docstring. Drives the flags panel's "first-ever check" group note,
    # scoped to the originality_reuse group only.
    first_upload_context: bool = False
    # BUG-078 (`ux-critic` finding, live-reproduced): mirrors FlagOut's own
    # field -- without this, the panel's `overridden` pill read "Overridden"
    # for a flag the instructor actually CONFIRMED, conflating exactly what
    # `FlagDetail.tsx`'s own terminal banner is careful never to conflate
    # (confirming isn't disagreeing with anything).
    confirmed_citation_source: bool = False


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


class DocumentParagraphOut(BaseModel):
    """V-065 AC1 (DOCX gap): one paragraph of the reconstructed-text pane.
    `paragraph` is `TextBlock.paragraph` verbatim (0-based body-item
    ordinal, `app/ingest/schemas.py`) -- the SAME number `¶{paragraph}`
    anchors already carry, so the frontend can match a flag's region to
    a paragraph by simple equality, no re-indexing. `heading_level` is
    cross-referenced from `section_tree` (1 = chapter), None for an
    ordinary body paragraph -- `ui-designer` spec (2026-08-22): lets the
    reader render real heading structure instead of an undifferentiated
    wall of text, a real accessibility gain the PDF pane has no
    equivalent for."""

    paragraph: int
    text: str
    heading_level: int | None


class DocumentParagraphsOut(BaseModel):
    paragraphs: list[DocumentParagraphOut]


class ExcludedReuseMatchOut(BaseModel):
    """V-072 (F7.4), `ui-designer` spec (2026-08-20) §4.2/§4.3: a passage
    match that the default policy excludes from scoring (own or matched
    side falls inside the reference list or a detected block quote) —
    revealed only when the instructor turns on the corresponding
    exploration toggle. NEVER a `Flag` row (never scored, never persisted
    as one) — `id` is a request-scoped string, not a `Flag.id`, so it can
    never be confused with one client-side.

    `own_region` reuses `FlagRegionOut`'s exact shape (including a
    `flag_id`, set to a synthetic negative int here per the spec's own
    call — reusing the existing type over inventing a near-duplicate one
    with `flag_id` removed) so `PdfPane`'s own highlight/anchor mechanism
    needs no widening to render it."""

    id: str
    own_excerpt: str
    own_context_before: str | None
    own_context_after: str | None
    own_region: FlagRegionOut
    matched_ref: int
    matched_excerpt: str
    matched_context_before: str | None
    matched_context_after: str | None
    context_words_each_side: int
    similarity: float
    level: str
    excluded_reason: list[Literal["reference_list", "block_quote"]]


class ReuseMatchesOut(BaseModel):
    """V-072 (F7.4): the exploration panel's data source. `archive_size_n`
    is the PASSAGE archive's own count (ticket AC5: "a thin archive must
    not make passage matching look authoritative") — always shown, even 0,
    same cold-start-honesty convention as every other `archive_size_n`
    already in this codebase."""

    passage_archive_size_n: int
    matches: list[ExcludedReuseMatchOut]


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
