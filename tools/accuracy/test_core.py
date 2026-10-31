"""Shared harness statistics."""

from tools.accuracy.core import cluster_bootstrap


def test_clustered_interval_is_wider_than_the_case_count_suggests():
    # Ten objectives, each with ten identical outcomes: the evidence is ten
    # clusters, not a hundred independent cases.
    outcomes = [(k, k < 7) for k in range(10) for _ in range(10)]
    low, high = cluster_bootstrap(outcomes, seed=1)
    assert low < 0.5 and high > 0.9


def test_one_cluster_gives_a_point_interval():
    assert cluster_bootstrap([("a", True), ("a", False)], seed=1) == (0.5, 0.5)


def test_no_cases_gives_no_interval():
    assert cluster_bootstrap([], seed=1) is None
