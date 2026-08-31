"""Audit log HTTP contract (F8.10, screen 4s)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


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
