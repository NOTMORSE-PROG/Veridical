"""Shared harness statistics."""

import json

from tools.accuracy.core import MODEL_RATERS, cluster_bootstrap, rate_row, real_precision_rows


def test_clustered_interval_is_wider_than_the_case_count_suggests():
    # Ten objectives, each with ten identical outcomes: the evidence is ten
    # clusters, not a hundred independent cases.
    outcomes = [(k, k < 7) for k in range(10) for _ in range(10)]
    low, high = cluster_bootstrap(outcomes, seed=1)
    assert low < 0.5 and high > 0.9


def test_a_point_interval_is_not_reported():
    # One cluster, or every cluster at the same rate: every resample is the
    # same, so the "interval" would claim certainty the data cannot give.
    assert cluster_bootstrap([("a", True), ("a", False)], seed=1) is None
    assert cluster_bootstrap([(k, True) for k in range(12)], seed=1) is None


def test_clusters_below_the_floor_make_a_row_indicative():
    row = rate_row("f4", "recall", "plant", 50, 60, clusters=14)
    assert row.indicative and row.extra["clusters"] == 14
    assert not rate_row("f4", "recall", "plant", 50, 60).indicative


def test_no_cases_gives_no_interval():
    assert cluster_bootstrap([], seed=1) is None


def _judged(tmp_path, judgments):
    (tmp_path / "f5").mkdir()
    (tmp_path / "f5" / "real_judgments.json").write_text(json.dumps(judgments), encoding="utf-8")
    return real_precision_rows(tmp_path, "f5")[0]


def test_real_precision_is_never_printed_as_human_judged_by_default(tmp_path):
    row = _judged(tmp_path, [{"finding": "x", "rater_a": "correct", "rater_b": "correct"}])
    assert MODEL_RATERS in row.note and "human tie-break" not in row.note
    assert row.extra["raters"] == MODEL_RATERS


def test_the_judgments_file_names_who_judged_only_when_every_item_agrees(tmp_path):
    judged = [
        {"finding": "x", "rater_a": "correct", "rater_b": "correct", "raters": "two instructors"},
        {"finding": "y", "rater_a": "correct", "rater_b": "correct"},
    ]
    assert _judged(tmp_path, judged).extra["raters"] == MODEL_RATERS
