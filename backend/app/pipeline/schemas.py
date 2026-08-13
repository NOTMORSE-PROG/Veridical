"""Check-run HTTP contracts (screens 4f/4g)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class CreateCheckRunRequest(BaseModel):
    manuscript_id: int
    rubric_id: int


class CheckRunOut(BaseModel):
    id: int
    manuscript_id: int
    rubric_id: int
    status: str
    stage_status: dict[str, Any] | None
    queue_position: int | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    # Display-only, for screen 4g (identifying which manuscript/rubric a
    # check is running against without the instructor holding an ID
    # number in their head). Optional: a manuscript/rubric could
    # theoretically be gone by the time this is read.
    manuscript_group_label: str | None = None
    # BUG-022: group_label alone can't distinguish two manuscripts
    # (defaults to "Ungrouped"); None if the manuscript predates this
    # column, or is gone.
    manuscript_original_filename: str | None = None
    manuscript_uploaded_at: datetime | None = None
    rubric_title: str | None = None

    model_config = {"from_attributes": True}
