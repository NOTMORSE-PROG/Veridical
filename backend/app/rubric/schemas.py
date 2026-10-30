"""Rubric decomposition contracts (F2.1): the LLM's structured-output
shape, and the persisted-criteria shape the API returns.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.db import DbInt, DbText


class LevelSource(BaseModel):
    """Where a level descriptor was found in the uploaded format (BUG-566).
    "exact": it is the whole text of the table cell named here, stored as
    that cell's text. "passage": one passage of the text outside tables.
    "unmatched": not traced to the document; the rubric carries a parse
    issue saying why. `table`/`row`/`col` are 1-based, as the decomposer saw
    them (empty columns dropped); `page` is the PDF page, None for DOCX."""

    match: Literal["exact", "passage", "unmatched"]
    page: DbInt | None = Field(default=None, ge=1)
    table: DbInt | None = Field(default=None, ge=1)
    row: DbInt | None = Field(default=None, ge=1)
    col: DbInt | None = Field(default=None, ge=1)
    cell_text: DbText | None = None


class ParsedLevel(BaseModel):
    """One rung of a graded performance scale (V-069 AC1) — structured,
    never squashed into `evidence_needed` prose. `level` is the scale's own
    ordinal (1-based, ascending); `points` is whatever numeric value the
    source document assigns that rung (not assumed to equal `level` — a
    source could weight its top rung higher than its ordinal position)."""

    level: DbInt = Field(ge=1)
    name: DbText = Field(min_length=1)
    descriptor: DbText = Field(min_length=1)
    points: float = Field(ge=0)
    # Set by the source check, never taken from the model's answer (the
    # decomposition overwrites whatever a response puts here). None on
    # levels stored before BUG-566, and then left out of the payload entirely.
    source: LevelSource | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("name", "descriptor")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class ParsedCriterion(BaseModel):
    """Exactly what the decomposition prompt is asked to return per
    criterion — validated before anything touches the database."""

    text: str = Field(min_length=1)
    type: Literal["structural", "semantic", "not_assessable"]
    evidence_needed: str | None = None
    weight: float = Field(gt=0)
    # V-069 AC1/AC3: present only for a criterion the source document
    # itself describes as a graded scale (>=2 named levels) — absent for
    # an ordinary pass/fail requirement, never invented (the prompt is
    # explicit: don't force a scale onto a format that doesn't have one).
    levels: list[ParsedLevel] | None = None

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("criterion text must not be blank")
        return stripped

    @field_validator("levels")
    @classmethod
    def _levels_are_a_real_scale(cls, value: list[ParsedLevel] | None) -> list[ParsedLevel] | None:
        if value is None:
            return None
        if len(value) < 2:
            raise ValueError("levels must describe at least 2 rungs, or be omitted entirely")
        ordinals = [lvl.level for lvl in value]
        if ordinals != sorted(ordinals) or len(set(ordinals)) != len(ordinals):
            raise ValueError("levels must have distinct, ascending `level` ordinals")
        # backend review finding (live-reproduced): an all-zero-points
        # scale makes `max_points` 0, which crashed the resolve endpoint's
        # points/max_points arithmetic with a raw `ZeroDivisionError`
        # instead of an honest, catchable error. Caught here at the
        # source, before it can ever reach a stored Criterion.
        if all(lvl.points <= 0 for lvl in value):
            raise ValueError("levels must have at least one rung worth more than 0 points")
        return value


class RubricDecomposition(BaseModel):
    """The full model response for one decomposition call."""

    criteria: list[ParsedCriterion] = Field(min_length=1)


class CriterionOut(BaseModel):
    id: int
    type: Literal["structural", "semantic", "not_assessable"]
    text: str
    evidence: str | None
    weight: float
    position: int
    levels: list[ParsedLevel] | None = None

    model_config = {"from_attributes": True}


class CitationRecencySettingsOut(BaseModel):
    """Effective values, each with where it came from ("format" when this
    format set it, "default" when the configured default applies)."""

    enabled: bool
    window_years: int
    reference_year_basis: Literal["manuscript_year", "check_date"]
    counts_toward_readiness: bool
    setting_sources: dict[str, Literal["format", "default"]]
    # True when the saved setting no longer validates. Checks then report
    # recency as unassessed rather than use the defaults shown here.
    saved_setting_invalid: bool = False


class ParseIssueDetail(BaseModel):
    """One parse issue. `code` says what kind: a level descriptor that is
    "not_in_source", keeps only part of its cell ("partial_cell"), "joins_cells",
    or comes from text that "differs_between_copies"; levels on the "wrong_row"
    or in the "wrong_order"; a "shared_cell" between criteria; a
    "level_left_out" of its row; or a whole-draft note ("draft_check",
    "retry_changed_draft", "unread_copy_text", "injection_suspected").
    `criterion_id`/`level` are null for whole-draft notes; `criterion_id`
    can name a criterion the instructor has since removed."""

    id: int
    code: str
    criterion_id: int | None = None
    level: int | None = None
    message: str


class RubricOut(BaseModel):
    id: int
    rubric_family_id: uuid.UUID
    version: int
    title: str
    # F2.2 (V-011): "needs_review" means retries were exhausted — the
    # review screen (V-012) shows `parse_issues` as the banner explaining
    # why, over whatever partial criteria list came back.
    parse_status: Literal["parsed", "needs_review"]
    parse_issues: list[str] | None
    # BUG-566: `parse_issues` again, one object per message in the same order,
    # saying which criterion and level each is about. None for rubrics parsed
    # before this field existed.
    parse_issue_details: list[ParseIssueDetail] | None = None
    # F2.3 (V-012): nothing runs against a rubric until the instructor
    # confirms — this flips true only via PUT /rubrics/{id}/criteria
    # {confirm: true} or POST /rubrics/{id}/activate.
    is_active: bool
    # F2.4 (V-013): false means a NEWER version exists in this rubric's
    # family — the review screen renders read-only for it (history is
    # immutable). A freshly uploaded, not-yet-confirmed rubric is always
    # its own family's only (hence latest) version, so this never blocks
    # the first-time confirm flow (V-012).
    is_latest_version: bool
    # Authoritative edit gate: history and any version referenced by a check
    # are immutable even when another version becomes active.
    can_edit_criteria: bool = False
    # V-064 (AC1): a family-level attribute (see the ticket/migration
    # 0026 for why it's denormalized this way). None = "Not set" -- never
    # guessed, same convention as `ManuscriptListItem.program`.
    program: str | None
    criteria: list[CriterionOut]
    # V-092: the citation recency values this format's next check will use.
    citation_recency: CitationRecencySettingsOut | None = Field(
        default=None, validation_alias="citation_recency_settings"
    )

    model_config = {"from_attributes": True}


class RubricListItem(BaseModel):
    """One row of a version list (screen 4m) — no full criteria payload,
    just what the list needs."""

    id: int
    rubric_family_id: uuid.UUID
    version: int
    title: str
    is_active: bool
    created_at: datetime
    criteria_count: int
    report_count: int
    program: str | None = None


class CriterionIn(BaseModel):
    """One row of the editable criteria table (screen 4d). `id` present =
    update an existing criterion; `id` absent/null = a new one the
    instructor added by hand."""

    id: DbInt | None = None
    type: Literal["structural", "semantic", "not_assessable"]
    text: DbText = Field(min_length=1)
    evidence: DbText | None = None
    weight: float = Field(gt=0)
    # V-069: round-tripped, never hand-edited on this screen in this
    # ticket (no AC requires per-level editing UI) — the instructor's
    # Save/Confirm action must not silently strip a decomposed scale back
    # down to prose, which is exactly what omitting this field here would
    # do (`update_criteria` REPLACES the full criteria set every save).
    levels: list[ParsedLevel] | None = None

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("criterion text must not be blank")
        return stripped


class SetRubricFamilyProgramRequest(BaseModel):
    """PUT /rubric-families/{family_id}/program body (V-064, AC1).
    `program_id: null` explicitly clears it back to "Not set"."""

    program_id: DbInt | None


class UpdateCriteriaRequest(BaseModel):
    """PUT /rubrics/{id}/criteria body: the full edited criteria set
    (replace, not patch — the review table always saves its whole
    state) and whether this save also confirms the rubric."""

    criteria: list[CriterionIn] = Field(min_length=1)
    confirm: bool = False
