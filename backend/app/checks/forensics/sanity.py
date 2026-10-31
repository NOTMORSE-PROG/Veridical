"""Sanity checks (F6.6, V-033): do a table's own percentages sum to
~100%, and do per-group sample sizes exceed a stated total? Pure
arithmetic over V-031's extracted descriptive stats — D-003's "wrap
validated libraries" applies to STATISTICAL METHODS (GRIM/GRIMMER/
p-recalculation); "do these percentages add up" is basic arithmetic, not
a method with a library to wrap.
"""

import re

from app.checks.forensics.checks import ForensicsFlagDraft
from app.checks.forensics.extract import ReportedStat
from app.checks.forensics.keywords import ForensicsKeywords, load_keywords
from app.checks.forensics.percentage_policy import PercentageSumPolicy, load_percentage_policy
from app.ingest.schemas import TableBlock
from app.models.enums import FlagSeverity

# Percentage points of slack for rounding across a handful of categories
# (each category independently rounded to whole/1dp can drift the sum by
# up to ~0.5pt per category) — not a statistical threshold, a rounding
# tolerance; kept as a named constant rather than a magic number inline.
PERCENT_SUM_TOLERANCE = 1.0

PERCENT_SUM_WORDING = (
    "The percentages in this table sum to {total:.1f}%, not 100% (±"
    "{tolerance:.0f} pt rounding tolerance). Possible arithmetic error "
    "or a missing/mislabeled category."
)
GROUP_COUNT_EXCEEDS_TOTAL_WORDING = (
    "The reported group sample sizes sum to {group_sum} but the stated "
    "total is {total}. Possible arithmetic error or a double-counted "
    "group."
)


def _by_anchor(stats: list[ReportedStat], stat_name: str) -> dict[str, list[ReportedStat]]:
    grouped: dict[str, list[ReportedStat]] = {}
    for stat in stats:
        if stat.kind == "descriptive" and stat.stat_name == stat_name and not stat.low_confidence:
            grouped.setdefault(stat.anchor, []).append(stat)
    return grouped


def _label(stat: ReportedStat) -> str:
    return (stat.group_label or "").strip().casefold()


def _plain_label(stat: ReportedStat) -> str:
    """The row label without a trailing note or punctuation: 'Total (n=120):'
    reads as 'total'."""
    label = re.sub(r"\([^)]*\)", " ", stat.group_label or "")
    return re.sub(r"[^\w\s]", " ", label).strip().casefold()


def _has_cue(text: str, cues: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(re.search(rf"(?<!\w){re.escape(cue)}s?(?!\w)", folded) for cue in cues)


def _share_matches(
    parts: list[ReportedStat], counts: list[ReportedStat], policy: PercentageSumPolicy
) -> bool:
    """Whether the table's own frequencies reproduce its percentages: the same
    denominator, so the rows are shares of one group."""
    labels = [_label(c) for c in counts]
    if len(set(labels)) != len(labels) or "" in labels:
        return False  # cannot pair a count with its row without guessing
    count_by_label = dict(zip(labels, (c.value for c in counts), strict=True))
    paired = [(r.value, count_by_label[_label(r)]) for r in parts if _label(r) in count_by_label]
    denominator = sum(count for _, count in paired)
    if len(paired) < policy.share_match_min_rows or denominator <= 0:
        return False
    matched = sum(
        abs(100.0 * count / denominator - pct) <= policy.share_match_tolerance
        for pct, count in paired
    )
    return matched / len(paired) >= policy.share_match_min_fraction


def evaluate_percentage_sums(
    stats: list[ReportedStat],
    *,
    tolerance: float = PERCENT_SUM_TOLERANCE,
    tables: list[TableBlock] | None = None,
    keywords: ForensicsKeywords | None = None,
    policy: PercentageSumPolicy | None = None,
) -> list[ForensicsFlagDraft]:
    """Flag a table whose percentages are evidently parts of one whole but do
    not total 100%. A table of independent rates (penetration, growth, pass
    rates) is never summed: the check needs a total row, a "total" or "100%"
    header, frequencies that reproduce the shares, or values that already add
    up to roughly 100. Precision first: when none of these shows, skip."""
    keywords = keywords or load_keywords()
    policy = policy or load_percentage_policy()
    flags: list[ForensicsFlagDraft] = []
    groups: dict[tuple[str, int | None], list[ReportedStat]] = {}
    for stat in stats:
        if stat.kind == "descriptive" and not stat.low_confidence and stat.stat_name:
            groups.setdefault((stat.anchor, stat.table_index), []).append(stat)
    for (anchor, table_index), members in groups.items():
        parts = [
            m
            for m in members
            if m.stat_name == "percentage"
            and _label(m) not in keywords.total_labels
            and _plain_label(m) not in policy.total_row_labels
        ]
        total_rows = [m for m in members if _plain_label(m) in policy.total_row_labels]
        if sum(m.stat_name == "percentage" for m in total_rows) > 1:
            continue  # several totals: sub-sections, not one distribution
        if len(parts) < 2:
            continue  # one percentage alone says nothing about "sums to 100"
        if any(not 0 <= r.value <= policy.max_part_percent for r in parts):
            continue  # a share of a whole cannot exceed 100: these are rates
        header: list[str] = []
        caption = ""
        if tables is not None and table_index is not None and table_index < len(tables):
            header = tables[table_index].rows[0] if tables[table_index].rows else []
            caption = tables[table_index].caption or ""
        header_text = " | ".join(header)
        if _has_cue(f"{header_text} | {caption}", policy.independent_header_cues):
            continue
        total = sum(row.value for row in parts)
        counts = [
            m for m in members if m.stat_name == "n" and _label(m) not in keywords.total_labels
        ]
        basis = next(
            (
                name
                for name, shown in (
                    ("total_row", bool(total_rows)),
                    ("whole_header", _has_cue(header_text, policy.whole_header_cues)),
                    ("frequencies_match", _share_matches(parts, counts, policy)),
                    ("near_whole", abs(total - 100.0) <= policy.near_whole_band),
                )
                if shown
            ),
            None,
        )
        if basis is None:
            continue
        if abs(total - 100.0) > tolerance:
            excerpt = ", ".join(f"{r.group_label or '?'}: {r.raw_text}" for r in parts)
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
                        "whole_basis": basis,
                        "policy_version": policy.version,
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
