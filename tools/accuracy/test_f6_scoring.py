"""F6 scoring credits a finding only to the plant it is about."""

from types import SimpleNamespace

from tools.accuracy.lanes.f6 import match_findings


def _flag(kind, page, excerpt):
    return SimpleNamespace(
        detail={"kind": kind}, page_anchor=f"p. {page}", evidence_excerpt=excerpt, severity="low"
    )


def _plant(kind, page, token):
    return {"type": kind, "page": page, "token": token, "split": "holdout"}


def test_a_false_alarm_on_a_control_is_not_matched_when_an_error_shares_the_copy():
    plants = [_plant("p_nongross", 10, "t(40) = 2.10"), _plant("p_correct", 20, "t(50) = 1.20")]
    fresh = [
        _flag("p_value_mismatch", 10, "t(40) = 2.10, p = .010"),
        _flag("p_value_mismatch", 20, "t(50) = 1.20, p = .235"),
    ]
    records, matched = match_findings(plants, fresh)
    assert matched == {0}
    assert [r["detected"] for r in records] == [True, True]  # the control drew an alarm


def test_a_gross_error_needs_the_decision_error_finding():
    plants = [_plant("p_gross", 10, "r(135) = .15")]
    weaker = [_flag("p_value_mismatch", 10, "r(135) = .15, p = .030")]
    records, matched = match_findings(plants, weaker)
    assert records[0]["detected"] is False
    assert matched == {0}, "the weaker finding still names a real error"
    records, _ = match_findings(plants, [_flag("p_value_decision_error", 10, "r(135) = .15")])
    assert records[0]["detected"] is True
