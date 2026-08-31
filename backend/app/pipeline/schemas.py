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
    cancel_requested_at: datetime | None
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


class RerunEstimateItem(BaseModel):
    manuscript_id: int
    # None when this manuscript has no prior DONE run to estimate from —
    # never a fabricated number (charter rule 9).
    estimated_calls: int | None


class RerunEstimateOut(BaseModel):
    """V-041: shown BEFORE confirming a bulk re-run (D-001 quota
    discipline). Each manuscript's own most recent DONE run's REAL
    measured call count (llm_call + llm_cache_hit audit rows) is the
    estimate for a re-run's cost -- a better predictor than any
    first-principles model of the pipeline's batching/self-consistency/
    integrity-check call structure, since it's the same document with
    the same section structure."""

    items: list[RerunEstimateItem]
    total_estimated_calls: int
    # Disclosed, not hidden: how many of the requested manuscripts had no
    # prior run to estimate from (excluded from the total above).
    manuscripts_with_no_estimate: int
