"""Criterion weight -> Low/Medium/High importance (D-023, BUG-051/052/098).

Weight is a RELATIVE value (`report/scoring.py` normalises it; no required
total), so rendering it as a bare percentage asserts a scale it doesn't have
-- that's what produced "WT. 999%" and totals of 1082%. Bucketing it into the
same 3-tier scale flag severity already uses (`SeverityTag.tsx`) sidesteps
the defect at its root instead of just formatting the number more carefully.

Expressed relative to the rubric's own EQUAL-SPLIT average (weight_total / n)
so the bucketing adapts to any rubric size -- a criterion weighted "about as
much as an equal share would be" is always Medium, regardless of whether the
rubric has 4 criteria or 60.
"""

from typing import Literal

from app.config import Settings

WeightImportance = Literal["low", "med", "high"]


def weight_importance(
    weight: float, *, average_weight: float, settings: Settings
) -> WeightImportance:
    """`average_weight <= 0` (no other criteria, or a zero-weight edge
    case) can't happen for a real persisted criterion (API-validated
    `> 0`, `app/rubric/schemas.py`), but is guarded rather than dividing
    by zero -- defaults to `med` (neither flatteringly high nor
    alarmingly low) since there is no real baseline to compare against."""
    if average_weight <= 0:
        return "med"
    ratio = weight / average_weight
    if ratio < settings.weight_importance_low_max_ratio:
        return "low"
    if ratio >= settings.weight_importance_high_min_ratio:
        return "high"
    return "med"
