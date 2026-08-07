"""Sanity checks (F6.6, V-033): do a table's own percentages sum to
~100%, and do per-group sample sizes exceed a stated total? Pure
arithmetic over V-031's extracted descriptive stats — D-003's "wrap
validated libraries" applies to STATISTICAL METHODS (GRIM/GRIMMER/
p-recalculation); "do these percentages add up" is basic arithmetic, not
a method with a library to wrap.
"""

from app.checks.forensics.checks import ForensicsFlagDraft
from app.checks.forensics.extract import ReportedStat
from app.checks.forensics.keywords import ForensicsKeywords, load_keywords
from app.models.enums import FlagSeverity

# Percentage points of slack for rounding across a handful of categories
# (each category independently rounded to whole/1dp can drift the sum by
# up to ~0.5pt per category) — not a statistical threshold, a rounding
# tolerance; kept as a named constant rather than a magic number inline.
PERCENT_SUM_TOLERANCE = 1.0

PERCENT_SUM_WORDING = (
    "The percentages in this table sum to {total:.1f}%, not 100% (±"
    "{tolerance:.0f} pt rounding tolerance) — possible arithmetic error "
    "or a missing/mislabeled category."
)
GROUP_COUNT_EXCEEDS_TOTAL_WORDING = (
    "The reported group sample sizes sum to {group_sum} but the stated "
    "total is {total} — possible arithmetic error or a double-counted "
    "group."
)


def _by_anchor(stats: list[ReportedStat], stat_name: str) -> dict[str, list[ReportedStat]]:
    grouped: dict[str, list[ReportedStat]] = {}
    for stat in stats:
        if stat.kind == "descriptive" and stat.stat_name == stat_name and not stat.low_confidence:
            grouped.setdefault(stat.anchor, []).append(stat)
    return grouped


def evaluate_percentage_sums(
    stats: list[ReportedStat], *, tolerance: float = PERCENT_SUM_TOLERANCE
) -> list[ForensicsFlagDraft]:
    flags: list[ForensicsFlagDraft] = []
    for anchor, rows in _by_anchor(stats, "percentage").items():
        if len(rows) < 2:
            continue  # one percentage alone says nothing about "sums to 100"
        total = sum(row.value for row in rows)
        if abs(total - 100.0) > tolerance:
            excerpt = ", ".join(f"{r.group_label or '?'}: {r.raw_text}" for r in rows)
            flags.append(
                ForensicsFlagDraft(
                    severity=FlagSeverity.low,
                    evidence_excerpt=excerpt,
                    page_anchor=anchor,
                    detail={
                        "kind": "percentage_sum_off",
                        "reason": PERCENT_SUM_WORDING.format(total=total, tolerance=tolerance),
                        "total": total,
                        "tolerance": tolerance,
                    },
                )
            )
    return flags


def evaluate_group_counts(
    stats: list[ReportedStat], keywords: ForensicsKeywords | None = None
) -> list[ForensicsFlagDraft]:
    keywords = keywords or load_keywords()
    flags: list[ForensicsFlagDraft] = []
    for anchor, rows in _by_anchor(stats, "n").items():
        total_rows = [
            r for r in rows if (r.group_label or "").strip().casefold() in keywords.total_labels
        ]
        group_rows = [r for r in rows if r not in total_rows]
        if not total_rows or len(group_rows) < 2:
            continue  # no stated total, or nothing to sum against it
        total_stated = total_rows[0].value
        group_sum = sum(r.value for r in group_rows)
        if group_sum > total_stated + 0.5:  # float-noise tolerance, not a rounding rule
            excerpt = ", ".join(f"{r.group_label or '?'}: {r.raw_text}" for r in group_rows)
            excerpt += f"; {total_rows[0].group_label}: {total_rows[0].raw_text}"
            flags.append(
                ForensicsFlagDraft(
                    severity=FlagSeverity.low,
                    evidence_excerpt=excerpt,
                    page_anchor=anchor,
                    detail={
                        "kind": "group_count_exceeds_total",
                        "reason": GROUP_COUNT_EXCEEDS_TOTAL_WORDING.format(
                            group_sum=int(group_sum), total=int(total_stated)
                        ),
                        "group_sum": group_sum,
                        "total": total_stated,
                    },
                )
            )
    return flags
