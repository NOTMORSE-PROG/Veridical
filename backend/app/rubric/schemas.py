"""Rubric decomposition contracts (F2.1): the LLM's structured-output
shape, and the persisted-criteria shape the API returns.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ParsedLevel(BaseModel):
    """One rung of a graded performance scale (V-069 AC1) — structured,
    never squashed into `evidence_needed` prose. `level` is the scale's own
    ordinal (1-based, ascending); `points` is whatever numeric value the
    source document assigns that rung (not assumed to equal `level` — a
    source could weight its top rung higher than its ordinal position)."""

    level: int = Field(ge=1)
    name: str = Field(min_length=1)
    descriptor: str = Field(min_length=1)
    points: float = Field(ge=0)

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
        # `backend-critic` finding (live-reproduced): an all-zero-points
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
    # V-064 (AC1): a family-level attribute (see the ticket/migration
    # 0026 for why it's denormalized this way). None = "Not set" -- never
    # guessed, same convention as `ManuscriptListItem.program`.
    program: str | None
    criteria: list[CriterionOut]

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

    id: int | None = None
    type: Literal["structural", "semantic", "not_assessable"]
    text: str = Field(min_length=1)
    evidence: str | None = None
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

    program_id: int | None


class UpdateCriteriaRequest(BaseModel):
    """PUT /rubrics/{id}/criteria body: the full edited criteria set
    (replace, not patch — the review table always saves its whole
    state) and whether this save also confirms the rubric."""

    criteria: list[CriterionIn] = Field(min_length=1)
    confirm: bool = False
