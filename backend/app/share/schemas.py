from datetime import datetime

from pydantic import BaseModel

from app.report.schemas import FlagSummaryOut, ReportOut


class CreateShareLinkIn(BaseModel):
    # Optional per FEATURES' own "revocable, optional expiry" -- no
    # default duration is invented; a bare None means "no expiry", the
    # instructor's own explicit choice, never a silently-assumed one.
    expires_at: datetime | None = None


class ShareLinkOut(BaseModel):
    token: str
    check_run_id: int
    created_at: datetime
    expires_at: datetime | None


class SharedReportOut(BaseModel):
    """The public, unauthenticated adviser view's payload (screen 4l).
    Deliberately just `ReportOut` + the same reduced `FlagSummaryOut`
    list the instructor's own Flags panel uses (BUG-033) -- no separate,
    independently-drifting "adviser" schema. Everything NOT safe for a
    logged-out reader (annotations, overrides, the audit trail, the
    dashboard) is excluded by simply never being reachable through this
    router, not by a second field-by-field allow-list to keep in sync."""

    report: ReportOut
    flags: list[FlagSummaryOut]
