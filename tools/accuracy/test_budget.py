"""The shared Gemini budget never lets a lane or the day overshoot."""

import threading
from datetime import datetime

import pytest
from tools.accuracy.budget import QUOTA_TIMEZONE, Budget, BudgetExceeded, pacific_day

MORNING = datetime(2026, 10, 31, 9, 0, tzinfo=QUOTA_TIMEZONE)


def test_a_lane_stops_at_its_share(tmp_path):
    budget = Budget(tmp_path / "budget.json", cap=200, shares={"f4": 2, "grading": 3})
    assert budget.reserve("f4", now=MORNING) == 1
    assert budget.reserve("f4", now=MORNING) == 2
    with pytest.raises(BudgetExceeded):
        budget.reserve("f4", now=MORNING)
    assert budget.reserve("grading", now=MORNING) == 1


def test_the_day_cap_holds_across_lanes(tmp_path):
    budget = Budget(tmp_path / "budget.json", cap=3, shares={"f4": 5, "grading": 5})
    budget.reserve("f4", 2, now=MORNING)
    with pytest.raises(BudgetExceeded):
        budget.reserve("grading", 2, now=MORNING)


def test_a_new_pacific_day_starts_fresh(tmp_path):
    budget = Budget(tmp_path / "budget.json", cap=10, shares={"f4": 1})
    budget.reserve("f4", now=MORNING)
    tomorrow = datetime(2026, 11, 1, 9, 0, tzinfo=QUOTA_TIMEZONE)
    assert budget.reserve("f4", now=tomorrow) == 1
    assert pacific_day(tomorrow) == "2026-11-01"


def test_concurrent_reservations_never_overshoot(tmp_path):
    budget = Budget(tmp_path / "budget.json", cap=50, shares={"f4": 50})
    granted, refused = [], []

    def worker():
        for _ in range(10):
            try:
                granted.append(budget.reserve("f4", now=MORNING))
            except BudgetExceeded:
                refused.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(granted) == 50 and len(refused) == 30
    assert sorted(granted) == list(range(1, 51))
