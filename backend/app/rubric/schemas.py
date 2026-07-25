"""Rubric decomposition contracts (F2.1): the LLM's structured-output
shape, and the persisted-criteria shape the API returns.
"""

import uuid
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
    criteria: list[CriterionOut]

    model_config = {"from_attributes": True}
