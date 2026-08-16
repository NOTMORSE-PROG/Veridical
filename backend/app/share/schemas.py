from datetime import datetime

from pydantic import BaseModel

from app.report.schemas import FlagSummaryOut, PublicReportOut


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

    BUG-044 (High, live-reproduced): this used to be typed as `ReportOut`
    itself — the instructor-facing model — on the theory that anything
    unsafe was "excluded by simply never being reachable through this
    router." That claim was false: `ReportOut` is shared and growing
    (V-041 added `previous_status`/`previous_composite_score` for an
    unrelated feature and both were silently published here), and the
    per-criterion `resolution` field — the instructor's own private
    reasoning for overriding an AI verdict — was live and readable with
    no cookie. Now `PublicReportOut`: an explicit, minimal, independently
    -typed projection (`app/report/schemas.py`) that must be deliberately
    extended, never one that grows for free."""

    report: PublicReportOut
    flags: list[FlagSummaryOut]
