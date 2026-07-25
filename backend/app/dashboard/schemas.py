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
