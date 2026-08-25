"""V-069 AC2: the levelled rubric's OWN institutional RATING formula
(e.g. TIP-VPAA-054B's `RATING = TotalScore / 36 x 100%`), computed as a
pure function separate from `report/scoring.py`'s composite.

Deliberately never merged into the composite: the two measure different
things and may legitimately disagree (composite applies flag deductions
and the instructor's own edited weights; RATING is the raw sum of the
rubric's own points) -- shown side by side, each clearly attributed
(ticket edge case: "must reconcile ... when they disagree", not "must
agree"). `None` whenever no criterion in the rubric is levelled, so a
pass/fail rubric's report/export payload gains no new visible field
(V-069 AC3).

Owner-approved treatment (AskUserQuestion, 2026-08-25): the per-criterion
LEVEL NAME is the judgment the instructor reads (report/service.py); this
RATING percentage is rendered only as a small, clearly-labeled
transcription of the institution's own formula, attributed to the rubric
-- never as a VERIDICAL judgment, and never in place of the banded
readiness verdict (ground rule 8).
"""

from dataclasses import dataclass
from typing import Any

from app.checks.levels import is_levelled, max_points


@dataclass(frozen=True)
class LevelledRating:
    achieved_points: float
    max_points: float
    rating_percent: float
    # Honesty floor (ground rule 9): an instructor reading "31/36 = 86.1%"
    # must be able to tell whether that's every criterion or a partial
    # sum — never presented as the full formula when it isn't.
    n_decided: int
    n_levelled: int


def compute_levelled_rating(
    criteria: list[Any], results_by_criterion_id: dict[int, Any]
) -> LevelledRating | None:
    """`results_by_criterion_id` maps criterion id -> its persisted
    `CheckResult` (or any object exposing `.detail`) for THIS check run.
    A criterion with no result yet, or a result whose `detail` carries no
    `"level"` (escalated/not_applicable/unresolved), contributes its max
    points to the denominator but nothing to the numerator -- the same
    "excluded, never guessed" honesty `report/scoring.py`'s composite
    already applies, just computed against the rubric's own formula
    instead of the weighted average."""
    levelled = [c for c in criteria if is_levelled(c)]
    if not levelled:
        return None

    achieved = 0.0
    total_max = 0.0
    n_decided = 0
    for criterion in levelled:
        criterion_max = max_points(criterion) or 0.0
        total_max += criterion_max
        result = results_by_criterion_id.get(criterion.id)
        detail = (getattr(result, "detail", None) or {}) if result is not None else {}
        level_detail = detail.get("level")
        if level_detail is not None:
            achieved += float(level_detail["points"])
            n_decided += 1

    rating_percent = round(achieved / total_max * 100.0, 1) if total_max > 0 else 0.0
    return LevelledRating(
        achieved_points=achieved,
        max_points=total_max,
        rating_percent=rating_percent,
        n_decided=n_decided,
        n_levelled=len(levelled),
    )
