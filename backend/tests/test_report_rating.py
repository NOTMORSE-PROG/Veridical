"""V-069 unit tests: `app.report.rating.compute_levelled_rating` — pure
function, no DB. Table-driven against hand-computed examples, same
convention as `test_report_scoring.py`.
"""

from dataclasses import dataclass, field

from app.report.rating import compute_levelled_rating


@dataclass
class FakeCriterion:
    id: int
    levels: list[dict] | None = field(default=None)


@dataclass
class FakeResult:
    detail: dict | None = None


TIP_SCALE = [
    {"level": 1, "name": "Beginner", "descriptor": "...", "points": 1},
    {"level": 2, "name": "Acceptable", "descriptor": "...", "points": 2},
    {"level": 3, "name": "Proficient", "descriptor": "...", "points": 3},
    {"level": 4, "name": "Exemplary", "descriptor": "...", "points": 4},
]


def _level_detail(name: str, ordinal: int, points: float, max_points: float = 4.0) -> dict:
    return {"name": name, "ordinal": ordinal, "points": points, "max_points": max_points}


def test_none_when_no_criterion_is_levelled():
    criteria = [FakeCriterion(id=1, levels=None), FakeCriterion(id=2, levels=None)]
    assert compute_levelled_rating(criteria, {}) is None


def test_real_tip_shaped_rubric_all_nine_decided():
    # 9 criteria x 4-point scale = 36 max, mirroring TIP-VPAA-054B's own
    # RATING = TotalScore/36 x 100% formula (the ticket's own example).
    criteria = [FakeCriterion(id=i, levels=TIP_SCALE) for i in range(1, 10)]
    # Every criterion decided at "Proficient" (3/4) except one Exemplary (4/4).
    results = {
        i: FakeResult(detail={"level": _level_detail("Proficient", 3, 3.0)}) for i in range(1, 9)
    }
    results[9] = FakeResult(detail={"level": _level_detail("Exemplary", 4, 4.0)})
    rating = compute_levelled_rating(criteria, results)
    assert rating is not None
    assert rating.max_points == 36.0
    assert rating.achieved_points == 3.0 * 8 + 4.0
    assert rating.n_decided == 9
    assert rating.n_levelled == 9
    assert rating.rating_percent == round((28.0 / 36.0) * 100, 1)


def test_undecided_criteria_count_toward_max_but_not_achieved():
    # Honesty floor: a criterion still escalated (no "level" in its
    # detail) contributes its max points to the denominator but nothing
    # to the numerator -- never guessed, never silently dropped either.
    criteria = [FakeCriterion(id=1, levels=TIP_SCALE), FakeCriterion(id=2, levels=TIP_SCALE)]
    results = {1: FakeResult(detail={"level": _level_detail("Exemplary", 4, 4.0)})}
    # criterion 2 has no result at all (still escalated, unresolved)
    rating = compute_levelled_rating(criteria, results)
    assert rating.max_points == 8.0
    assert rating.achieved_points == 4.0
    assert rating.n_decided == 1
    assert rating.n_levelled == 2
    assert rating.rating_percent == 50.0


def test_mixed_levelled_and_pass_fail_rubric_only_counts_levelled():
    # Edge case (ticket): a rubric mixing levelled and pass/fail criteria
    # -- the RATING formula only concerns the levelled subset, never
    # pulled toward 100 by a pass/fail criterion that has no points scale.
    criteria = [
        FakeCriterion(id=1, levels=TIP_SCALE),
        FakeCriterion(id=2, levels=None),  # ordinary pass/fail, ignored here
    ]
    results = {1: FakeResult(detail={"level": _level_detail("Proficient", 3, 3.0)})}
    rating = compute_levelled_rating(criteria, results)
    assert rating.max_points == 4.0
    assert rating.n_levelled == 1


def test_zero_max_points_does_not_divide_by_zero():
    # Defensive: a levelled criterion always has >=2 rungs (ParsedLevel's
    # own validator), so max_points is never really 0 in practice -- but
    # this function must not crash if it ever were.
    criteria = [FakeCriterion(id=1, levels=[])]
    rating = compute_levelled_rating(criteria, {})
    assert rating is None  # is_levelled([]) is False -- not levelled at all
