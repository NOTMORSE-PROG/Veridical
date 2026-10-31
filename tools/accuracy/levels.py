"""Per-level scoring for ordered grading levels.

A grader that always answers the most common level must not be able to look
accurate. So a planted grading set has to be balanced across the levels, the
report gives recall level by level with Wilson intervals, and the full confusion
matrix is printed so the direction of every miss is visible.
"""

from __future__ import annotations

from collections import Counter

from tools.accuracy.core import MetricRow, rate_row

# A level holding less than this share of the cases is too thin to score.
MIN_LEVEL_SHARE = 0.15


def balance_problems(expected: list[str], levels: list[str]) -> list[str]:
    """Why a planted set is not balanced enough to report, or an empty list."""
    counts = Counter(expected)
    total = len(expected)
    problems = [f"unknown level {name!r}" for name in sorted(set(counts) - set(levels))]
    if not total:
        return ["no cases"]
    for level in levels:
        share = counts.get(level, 0) / total
        if share < MIN_LEVEL_SHARE:
            problems.append(f"{level} holds {counts.get(level, 0)} of {total} cases")
    return problems


def confusion(pairs: list[tuple[str, str | None]], levels: list[str]) -> dict:
    """Rows are the planted level, columns the answer. `None` (no answer, escalated)
    gets its own column rather than being counted as a miss in the wrong direction."""
    columns = [*levels, None]
    table = {row: {str(col): 0 for col in columns} for row in levels}
    for expected, got in pairs:
        table[expected][str(got)] += 1
    return table


def baseline_accuracy(expected: list[str]) -> float | None:
    """Accuracy of always answering the most common planted level."""
    if not expected:
        return None
    return Counter(expected).most_common(1)[0][1] / len(expected)


def level_rows(lane: str, pairs: list[tuple[str, str | None]], levels: list[str], *, split="all"):
    rows: list[MetricRow] = []
    for level in levels:
        mine = [(e, g) for e, g in pairs if e == level]
        hit = sum(1 for e, g in mine if g == e)
        rows.append(
            rate_row(
                lane,
                f"recall at level {level}",
                "planted case",
                hit,
                len(mine),
                split=split,
                note="Answers matching the planted level exactly.",
            )
        )
    return rows
