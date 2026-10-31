"""Level scoring cannot be gamed by always answering one level."""

from tools.accuracy.levels import balance_problems, baseline_accuracy, confusion, level_rows

LEVELS = ["Beginner", "Acceptable", "Proficient", "Exemplary"]


def test_a_skewed_set_is_refused():
    expected = ["Proficient"] * 9 + ["Beginner"]
    problems = balance_problems(expected, LEVELS)
    assert any("Exemplary" in p for p in problems)
    assert any("Acceptable" in p for p in problems)


def test_a_balanced_set_passes():
    assert balance_problems(LEVELS * 5, LEVELS) == []


def test_always_proficient_scores_only_its_own_level():
    expected = LEVELS * 5
    pairs = [(e, "Proficient") for e in expected]
    rows = {r.metric: r for r in level_rows("grading", pairs, LEVELS)}
    assert rows["recall at level Proficient"].rate == 1.0
    assert rows["recall at level Beginner"].rate == 0.0
    assert baseline_accuracy(expected) == 0.25


def test_no_answer_has_its_own_column():
    table = confusion([("Beginner", None), ("Beginner", "Beginner")], LEVELS)
    assert table["Beginner"]["None"] == 1
    assert table["Beginner"]["Beginner"] == 1
