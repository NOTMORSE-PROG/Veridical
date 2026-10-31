"""Shared harness statistics."""

from tools.accuracy.core import cluster_bootstrap, rate_row


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
