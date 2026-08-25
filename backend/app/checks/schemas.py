"""Semantic grading contracts (F3.3): the LLM's structured-output shape
for one batched grading call.
"""

from pydantic import BaseModel, Field, field_validator


class GradeVerdict(BaseModel):
    """One criterion's verdict within a batch response. `index` ties it
    back to the criterion at that position in the request's own
    "--- CRITERIA ---" listing (stable within one call; never a DB id, so
    the model never has to echo one back correctly).

    V-069: `verdict` was `Literal["pass", "partial", "fail"]` until this
    ticket — relaxed to a plain string so a levelled criterion's own level
    NAME (e.g. "Proficient") validates too. This schema has no way to know
    which criterion index it belongs to, so it can't validate the string
    against that criterion's own scale here; `app.checks.levels.
    outcome_and_score` does that per-criterion check downstream and
    escalates an unrecognized string rather than guessing (charter rule 1)
    — a strictly safer fallback than the old Literal's `KeyError` on any
    value this validator wouldn't have let through in the first place."""

    index: int
    verdict: str = Field(min_length=1)
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
