"""Audit log HTTP contract (F8.10, screen 4s)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

# BUG-219: which model actually produced an `llm_*` row, derived server-side
# from the already-stored `fake_llm` fact so the list/detail surfaces never
# have to guess. Reuses BUG-049's own `fake`/`real`/`unknown` vocabulary
# (`app.models.enums.LLMMode`, already instructor-facing on the report/flag/
# adviser surfaces via `TestModeBanner`) rather than inventing a second one
# for the same fact — kept as its own Literal, not the ORM enum itself, to
# match this module's existing `str`-at-the-boundary convention (see
# `report/schemas.py`'s `llm_mode: str`). `None` for every non-LLM event
# type (the concept doesn't apply); "unknown" is a real, distinct state (an
# `llm_*` row written before this field existed carries neither
# `fake_llm: true` nor `fake_llm: false`) and must never be silently
# promoted to "real".
LLMExecutionMode = Literal["fake", "real", "unknown"]


class AuditLogSummary(BaseModel):
    """One row of the filterable list — the detail drawer opens the full
    payload separately so the list itself stays light even at volume
    (ticket AC: "10K rows, 4s stays responsive")."""

    id: int
    event_type: str
    check_run_id: int | None
    manuscript_id: int | None = None
    manuscript_group_label: str | None
    prompt_type: str | None
    prompt_version: str | None
    agreement_score: float | None
    llm_execution_mode: LLMExecutionMode | None
    created_at: datetime


class AuditLogDetail(AuditLogSummary):
    # BUG-022: only on the detail view, not the list rows (the list stays
    # deliberately light at volume, ticket AC: "10K rows, 4s stays
    # responsive") — group_label defaults to "Ungrouped" and can't
    # distinguish two manuscripts alone.
    manuscript_original_filename: str | None
    input_hash: str | None
    # Full raw payload (prompt, context, response) — the one sanctioned
    # place for it (CODING.md §1). The frontend caps the on-screen PREVIEW
    # (ticket edge case); this field itself is never truncated.
    payload: dict[str, Any]


class PaginatedAuditLog(BaseModel):
    items: list[AuditLogSummary]
    total: int
    page: int
    page_size: int
