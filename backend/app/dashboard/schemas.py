"""Dashboard KPI contract (screen 4e)."""

from pydantic import BaseModel


class DashboardStats(BaseModel):
    manuscripts_checked: int
    ready_count: int
    conditionally_ready_count: int
    not_ready_count: int
    needs_review_count: int
    escalations_awaiting_review: int
    # None when nothing has been graded yet — never a fabricated 0%
    # (charter rule 9).
    escalation_rate: float | None
    escalation_budget: float
    system_underperforming: bool
    # V-038 / ux-critic finding: how many of the (latest-done-run)
    # manuscripts above have a real recorded decision -- without this the
    # dashboard gave no signal at all that the terminal decision gate
    # (F8.5) had ever been used. Scoped to the exact same latest-done-run
    # set as every other count on this screen (BUG-012's own rule).
    decided_count: int
