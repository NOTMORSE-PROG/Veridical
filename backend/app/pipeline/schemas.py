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

    model_config = {"from_attributes": True}
