"""Semantic grading contracts (F3.3): the LLM's structured-output shape
for one batched grading call.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class GradeVerdict(BaseModel):
    """One criterion's verdict within a batch response. `index` ties it
    back to the criterion at that position in the request's own
    "--- CRITERIA ---" listing (stable within one call; never a DB id, so
    the model never has to echo one back correctly)."""

    index: int
    verdict: Literal["pass", "partial", "fail"]
    reasoning: str = Field(min_length=1)
    evidence_quotes: list[str] = Field(min_length=1)

    @field_validator("evidence_quotes")
    @classmethod
    def _no_blank_quotes(cls, value: list[str]) -> list[str]:
        cleaned = [q.strip() for q in value if q.strip()]
        if not cleaned:
            raise ValueError("evidence_quotes must contain at least one non-blank quote")
        return cleaned


class GradeBatchResponse(BaseModel):
    verdicts: list[GradeVerdict] = Field(min_length=1)
