"""Verdict agreement between F4 runs counts only plants judged in both."""

from tools.accuracy.f4_stability import compare


def _plant(verdict, kind="objective_not_achieved"):
    return {"verdict": verdict, "kind": kind}


def test_only_plants_judged_in_both_runs_are_compared():
    a = {"p1": _plant("contradictory"), "p2": _plant("consistent"), "p3": _plant("partial")}
    b = {"p1": _plant("contradictory"), "p2": _plant("partial")}
    result = compare(a, b)
    assert result["n"] == 2
    assert result["same_verdict"] == 1
    # consistent -> partial turns silence into a finding.
    assert result["same_flagged_or_silent"] == 1
    assert result["same_contradictory"] == 2
    assert result["changed"] == [
        {"plant": "p2", "kind": "objective_not_achieved", "a": "consistent", "b": "partial"}
    ]


def test_runs_with_no_plant_in_common_report_nothing():
    assert compare({"p1": _plant("partial")}, {"p2": _plant("partial")}) == {"n": 0}
