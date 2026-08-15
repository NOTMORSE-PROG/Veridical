from datetime import datetime

from pydantic import BaseModel


class ArchiveItemOut(BaseModel):
    manuscript_id: int
    group_label: str
    original_filename: str | None
    created_at: datetime
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
