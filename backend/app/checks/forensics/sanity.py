"""Sanity checks (F6.6, V-033): do a table's own percentages sum to
~100%, and do per-group sample sizes exceed a stated total? Pure
arithmetic over V-031's extracted descriptive stats — D-003's "wrap
validated libraries" applies to STATISTICAL METHODS (GRIM/GRIMMER/
p-recalculation); "do these percentages add up" is basic arithmetic, not
a method with a library to wrap.
"""

import re

from app.checks.forensics.checks import ForensicsFlagDraft
from app.checks.forensics.extract import ReportedStat, table_statistical_cells_complete
from app.checks.forensics.keywords import ForensicsKeywords, load_keywords
from app.checks.forensics.percentage_policy import PercentageSumPolicy, load_percentage_policy
from app.checks.forensics.table_context import has_pattern, load_table_context_policy
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


def evaluate_percentage_sums(
    stats: list[ReportedStat],
    *,
    tolerance: float = PERCENT_SUM_TOLERANCE,
    tables: list[TableBlock] | None = None,
    keywords: ForensicsKeywords | None = None,
    policy: PercentageSumPolicy | None = None,
    contexts: dict[int, dict] | None = None,
    assessment_records: list[dict] | None = None,
) -> list[ForensicsFlagDraft]:
    """Flag a table whose percentages are evidently parts of one whole but do
    not total 100%. A near-100 sum or matching frequencies cannot establish
    exhaustive, non-overlapping categories. Require a whole-table declaration
    and retain explicit overlap/incomplete/rate exclusions."""
    keywords = keywords or load_keywords()
    policy = policy or load_percentage_policy()
    context_policy = load_table_context_policy()
    flags: list[ForensicsFlagDraft] = []
    groups: dict[tuple[str, int | None], list[ReportedStat]] = {}
    for stat in stats:
        if stat.kind == "descriptive" and not stat.low_confidence and stat.stat_name:
            groups.setdefault((stat.anchor, stat.table_index), []).append(stat)
    for (anchor, table_index), members in groups.items():
        if not any(member.stat_name == "percentage" for member in members):
            continue
        context = (contexts or {}).get(table_index, {})
        record = {
            "table_index": table_index,
            "anchor": anchor,
            "state": "unassessed",
            "reason": "distribution_not_established",
            "context": context,
            "policy_version": policy.version,
        }
        if assessment_records is not None:
            assessment_records.append(record)
        parts = [
            m
            for m in members
            if m.stat_name == "percentage"
            and _label(m) not in keywords.total_labels
            and _plain_label(m) not in policy.total_row_labels
        ]
        total_rows = [m for m in members if _plain_label(m) in policy.total_row_labels]
        if sum(m.stat_name == "percentage" for m in total_rows) > 1:
            record["reason"] = "multiple_totals"
            continue  # several totals: sub-sections, not one distribution
        if len(parts) < 2:
            record["reason"] = "insufficient_categories"
            continue  # one percentage alone says nothing about "sums to 100"
        if any(not 0 <= r.value <= policy.max_part_percent for r in parts):
            record.update(state="excluded", reason="not_percentage_shares")
            continue  # a share of a whole cannot exceed 100: these are rates
        header: list[str] = []
        caption = ""
        if tables is not None and table_index is not None and table_index < len(tables):
            if not table_statistical_cells_complete(tables[table_index], keywords):
                record["reason"] = "incomplete_table_cells"
                continue
            header = tables[table_index].rows[0] if tables[table_index].rows else []
            caption = tables[table_index].caption or ""
        header_text = " | ".join(header)
        declaration = f"{header_text}\n{caption}\n{context.get('text', '')}"
        if _has_cue(declaration, policy.independent_header_cues) or has_pattern(
            declaration, context_policy["distribution_veto_patterns"]
        ):
            record.update(state="excluded", reason="independent_or_incomplete_categories")
            continue
        total = sum(row.value for row in parts)
        basis = next(
            (
                name
                for name, shown in (
                    ("total_row", bool(total_rows)),
                    ("whole_header", _has_cue(header_text, policy.whole_header_cues)),
                    (
                        "complete_distribution",
                        has_pattern(declaration, context_policy["complete_patterns"])
                        and has_pattern(declaration, context_policy["exclusive_patterns"]),
                    ),
                )
                if shown
            ),
            None,
        )
        if basis is None:
            continue
        record.update(state="assessed", reason=None, whole_basis=basis, total=total)
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
                        "distribution_context": context,
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
