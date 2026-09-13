from datetime import UTC, datetime

from pydantic import BaseModel, field_validator

from app.report.schemas import FlagSummaryOut, PublicReportOut


class CreateShareLinkIn(BaseModel):
    # Optional per FEATURES' own "revocable, optional expiry" -- no
    # default duration is invented; a bare None means "no expiry", the
    # instructor's own explicit choice, never a silently-assumed one.
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _reject_past_expiry(cls, value: datetime | None) -> datetime | None:
        # BUG-062: a past-dated expires_at was previously accepted (HTTP
        # 200), creating a link that 410s immediately -- a link "born
        # dead" is a footgun (the instructor sees a working "Create link"
        # flow with no signal anything is wrong until the recipient
        # reports a dead link), not a security hole (it does correctly
        # 410 rather than serving stale content).
        #
        # `backend-critic` finding: an earlier version of this compared a
        # naive `value` against `datetime.now()` -- the CONTAINER's own
        # ambient OS timezone, unpinned anywhere in this deploy, live-
        # reproduced to both silently accept an already-past UTC instant
        # and wrongly reject a genuinely future one, depending on which
        # `TZ` the process happened to be running under. The shipped
        # frontend always sends an offset-aware string (`.toISOString()`),
        # but this is a public API any other caller can reach. A naive
        # input is now treated as UTC explicitly, never compared against
        # ambient local wall-clock time.
        if value is not None:
            aware_value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
            if aware_value <= datetime.now(UTC):
                raise ValueError("Expiry must be in the future.")
        return value


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
