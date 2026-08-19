"""Rubric decomposition contracts (F2.1): the LLM's structured-output
shape, and the persisted-criteria shape the API returns.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ParsedCriterion(BaseModel):
    """Exactly what the decomposition prompt is asked to return per
    criterion — validated before anything touches the database."""

    text: str = Field(min_length=1)
    type: Literal["structural", "semantic"]
    evidence_needed: str | None = None
    weight: float = Field(gt=0)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("criterion text must not be blank")
        return stripped


class RubricDecomposition(BaseModel):
    """The full model response for one decomposition call."""

    criteria: list[ParsedCriterion] = Field(min_length=1)


class CriterionOut(BaseModel):
    id: int
    type: Literal["structural", "semantic"]
    text: str
    evidence: str | None
    weight: float
    position: int

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
    type: Literal["structural", "semantic"]
    text: str = Field(min_length=1)
    evidence: str | None = None
    weight: float = Field(gt=0)

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
