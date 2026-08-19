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


class GroupCollisionOut(BaseModel):
    """V-063 (owner's call, 2026-08-19): rule 3 of the matching rule
    (ticket's DECIDED section) says a same-short-name-zero-overlap case
    must be PROPOSED, not silently applied -- this is what lets the
    confirm form show that proposal BEFORE the instructor confirms,
    instead of only disclosing it after the fact via a disambiguating
    suffix on the PATCH response."""

    collision: bool
    existing_group_name: str | None = None
