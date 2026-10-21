from datetime import datetime

from pydantic import BaseModel

from app.storage.availability import AvailabilityBasis, AvailabilityState


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
    # Retention metadata only; a recorded path does not prove physical
    # availability. Source retrieval checks local/durable storage on demand.
    has_stored_file: bool
    # V-078: a legacy Library record built from this upload before V-077
    # (D-028); deleting the stored file also takes it out of the Library.
    in_library: bool
    # V-078: the newest check's id, so the page can link to its report
    # without repeating any of the report's content.
    latest_check_run_id: int | None
    latest_done_check_run_id: int | None


class PaginatedArchive(BaseModel):
    items: list[ArchiveItemOut]
    total: int
    page: int
    page_size: int


class PurgeOut(BaseModel):
    manuscript_id: int
    purged_at: datetime


class SourceAvailabilityOut(BaseModel):
    manuscript_id: int
    state: AvailabilityState
    basis: AvailabilityBasis | None = None
    checked_at: datetime
    purged_at: datetime | None = None
