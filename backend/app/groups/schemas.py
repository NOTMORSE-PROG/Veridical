"""HTTP response shapes for the groups/programs HTTP surface (V-062)."""

from pydantic import BaseModel


class ProgramOut(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class GroupOut(BaseModel):
    id: int
    name: str
    # NULL = "Not set" -- never guessed (same convention as
    # `ingest_failure_reason` / `ManuscriptListItem.program`).
    program: str | None = None
