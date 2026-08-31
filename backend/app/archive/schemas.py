from datetime import datetime

from pydantic import BaseModel


class ArchiveItemOut(BaseModel):
    manuscript_id: int
    group_label: str
    original_filename: str | None
    created_at: datetime
    # V-071 (BUG-058): this endpoint used to omit ingest_status entirely,
    # so a manuscript whose ingestion had failed showed as "Not checked
    # yet" here while the dashboard correctly read "Ingestion failed" for
    # the exact same row -- two screens disagreeing about the same file.
    ingest_status: str
    # V-071 AC4: non-NULL means a failed upload was removed from the active
    # Review Desk but retained here; it was not deleted or reclassified.
    dismissed_at: datetime | None
    latest_check_run_status: str | None
    # Whether the F7 whole-document embedding row still exists -- False
    # both for "never checked" and "purged"; `purged_at` distinguishes them.
    has_archive: bool
    purged_at: datetime | None


class PaginatedArchive(BaseModel):
    items: list[ArchiveItemOut]
    total: int
    page: int
    page_size: int


class PurgeOut(BaseModel):
    manuscript_id: int
    purged_at: datetime
