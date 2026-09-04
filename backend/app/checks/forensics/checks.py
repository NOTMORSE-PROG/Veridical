"""Applicability-gated GRIM/GRIMMER evaluation over V-031's extracted
descriptive stats (F6.2/F6.3, V-032) — where GRIM/GRIMMER math becomes
real, honestly-worded Flags.

**Applicability guard is the safety-critical part of this module**
(ticket's own instruction: "guessing the scale creates false accusations
— skip when unsure, log why", charter judgment #1: precision over
recall). A bare table row ("n=30, M=3.45, SD=0.62") carries no reliable
machine signal on whether the underlying data is integer-scale (GRIM/
GRIMMER only apply to the mean/SD of INTEGER item responses, e.g. a
Likert-style survey item or its multi-item average — never a continuous
measure or an already-computed composite score, ticket edge case). This
module's working assumption, stated plainly rather than silently
encoded: a descriptive stat extracted from a table (V-031) is treated as
POTENTIALLY integer-scale only when paired n+mean values exist for the
same table row — every candidate is still run through GRIM itself, whose
own math is the actual arbiter of consistency; nothing here decides
"looks like a Likert item" by guessing content. If manuscripts turn out
to report non-integer composite means in the same table shape, this
would need scale metadata this module doesn't have (from the rubric or
the section text) to gate correctly — logged as a known limitation, not
silently worked around.
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.checks.forensics.extract import ReportedStat
from app.checks.forensics.grim import grim_check, grimmer_check
from app.models.enums import FlagSeverity

GRIM_INCONSISTENT_WORDING = (
    "The reported mean ({mean}) appears mathematically inconsistent with "
    "the sample size (n = {n}), possible rounding error or typo. No "
    "integer combination of {n} responses averages to {mean} at the "
    "reported precision."
)
GRIMMER_INCONSISTENT_WORDING = (
    "The reported standard deviation ({sd}) appears mathematically "
    "inconsistent with the reported mean ({mean}) and sample size "
    "(n = {n}), possible rounding error or typo."
)


@dataclass(frozen=True)
class ForensicsFlagDraft:
    """Same shape as `app.checks.citations.extract.CitationFlagDraft`,
    named separately to keep the two check families' vocabularies
    distinct — kept here, not imported, since forensics flags are never
    citation-related."""

    severity: FlagSeverity
    evidence_excerpt: str
    page_anchor: str
    detail: dict[str, Any]


def _decimal_places(raw_text: str) -> int:
    match = re.search(r"\.(\d+)", raw_text)
    return len(match.group(1)) if match else 0


# BUG-164 (`backend-critic` finding, live-reproduced): `anchor` alone is
# a PAGE string, not a table identity -- two distinct tables printed on
# the same page (a common layout: several small single-row summary
# tables stacked together) share the identical anchor, so grouping on
# `(anchor, group_label)` alone silently merged rows from genuinely
# unrelated tables. `table_index` (real position within `ExtractionResult.
# tables`, `extract.py`'s own docstring for why it exists) disambiguates
# them without changing `anchor`'s own displayed string (still used
# as-is for `page_anchor`/`evidence_excerpt`).
_GroupKey = tuple[str, int | None, str | None]


def _group_descriptive_stats(stats: list[ReportedStat]) -> dict[_GroupKey, dict[str, ReportedStat]]:
    """Reconstructs table rows: every descriptive stat V-031 extracted
    from the SAME table row shares (anchor, table_index, group_label) —
    group them back into {n, mean, sd} triples per row."""
    grouped: dict[_GroupKey, dict[str, ReportedStat]] = {}
    for stat in stats:
        if stat.kind != "descriptive" or stat.low_confidence:
            continue  # V-007 contract, same as V-031's own extraction gate
        key = (stat.anchor, stat.table_index, stat.group_label)
        grouped.setdefault(key, {})[stat.stat_name] = stat
    return grouped


@dataclass(frozen=True)
class GrimGrimmerResult:
    flags: list[ForensicsFlagDraft]
    # BUG-164: rows deliberately never evaluated because they look like a
    # multi-item composite mean, not GRIM/GRIMMER's assumed single-item
    # mean -- disclosed here (surfaces in `CheckResult.detail`) rather
    # than silently omitted, the module's own "skip when unsure, log why"
    # discipline applied to the one case its own docstring named but
    # didn't implement.
    skipped_composite_rows: int


_TableKey = tuple[str, int | None]


def _likely_composite_tables(
    grouped: dict[_GroupKey, dict[str, ReportedStat]],
) -> set[_TableKey]:
    """BUG-164: GRIM/GRIMMER assume n respondents each contributing ONE
    integer item response -- violated by the single most common capstone
    table shape, a multi-item instrument summary (ISO 25010
    characteristics, a Likert survey) reporting a WEIGHTED mean of k
    sub-items over the SAME n respondents, once per criterion/indicator
    row. Measured: 67-81% false-positive rate on genuinely correct means
    when this assumption is violated (ticket's own 2,000-trial
    simulation). No scale/item-count metadata exists anywhere in this
    pipeline to know k directly (this module's own docstring is honest
    about that gap) -- but a real, purely STRUCTURAL signal is available
    without guessing content, matching this module's existing discipline
    of never deciding "looks like a Likert item" from table text: when
    the SAME n value repeats across two or more DIFFERENT rows of the
    SAME table, that table is reporting multiple criteria/items over one
    shared respondent pool, not independent single-item measurements
    each with their own n -- exactly the shape the ticket names.

    Keyed on `(anchor, table_index)`, NOT `anchor` alone (`backend-critic`
    finding, live-reproduced): `anchor` is a PAGE string, and two
    genuinely distinct, unrelated single-row tables printed on the same
    page -- a real, common layout -- would otherwise be merged into one
    "composite" group purely because they share a page and happen to
    report the same n, silently suppressing a real GRIM inconsistency in
    either one. Returns the set of tables where this holds; every row in
    such a table is skipped, honestly, rather than guessing the true
    item count."""
    n_counts_by_table: dict[_TableKey, Counter[int]] = {}
    for (anchor, table_index, _group_label), row in grouped.items():
        n_stat = row.get("n")
        if n_stat is None:
            continue
        n_counts_by_table.setdefault((anchor, table_index), Counter())[int(n_stat.value)] += 1
    return {
        table_key
        for table_key, counts in n_counts_by_table.items()
        if any(c >= 2 for c in counts.values())
    }


def evaluate_grim_grimmer(stats: list[ReportedStat]) -> GrimGrimmerResult:
    flags: list[ForensicsFlagDraft] = []
    grouped = _group_descriptive_stats(stats)
    composite_tables = _likely_composite_tables(grouped)
    skipped_composite_rows = 0

    for (anchor, table_index, group_label), row in grouped.items():
        n_stat = row.get("n")
        mean_stat = row.get("mean")
        if n_stat is None or mean_stat is None:
            continue  # GRIM needs both — no guessing a missing n or mean

        if (anchor, table_index) in composite_tables:
            skipped_composite_rows += 1
            continue

        n = int(n_stat.value)
        mean = mean_stat.value
        m_prec = _decimal_places(mean_stat.raw_text)
        label = f" ({group_label})" if group_label else ""

        if not grim_check(n, mean, prec=m_prec):
            flags.append(
                ForensicsFlagDraft(
                    severity=FlagSeverity.med,
                    evidence_excerpt=f"n={n_stat.raw_text}, M={mean_stat.raw_text}{label}",
                    page_anchor=anchor,
                    detail={
                        "kind": "grim_inconsistent",
                        "reason": GRIM_INCONSISTENT_WORDING.format(mean=mean_stat.raw_text, n=n),
                        "n": n,
                        "mean": mean,
                        "mean_precision": m_prec,
                    },
                )
            )
            continue  # GRIMMER can never pass when GRIM already fails (ported R behavior)

        sd_stat = row.get("sd")
        if sd_stat is None:
            continue  # GRIMMER needs an SD — skip, don't guess one

        sd = sd_stat.value
        sd_prec = _decimal_places(sd_stat.raw_text)
        grimmer_result = grimmer_check(mean, sd, n, m_prec=m_prec, sd_prec=sd_prec)
        if not grimmer_result.passed:
            flags.append(
                ForensicsFlagDraft(
                    severity=FlagSeverity.med,
                    evidence_excerpt=(
                        f"n={n_stat.raw_text}, M={mean_stat.raw_text}, SD={sd_stat.raw_text}{label}"
                    ),
                    page_anchor=anchor,
                    detail={
                        "kind": "grimmer_inconsistent",
                        "reason": GRIMMER_INCONSISTENT_WORDING.format(
                            sd=sd_stat.raw_text, mean=mean_stat.raw_text, n=n
                        ),
                        "n": n,
                        "mean": mean,
                        "sd": sd,
                        "mean_precision": m_prec,
                        "sd_precision": sd_prec,
                    },
                )
            )
    return GrimGrimmerResult(flags=flags, skipped_composite_rows=skipped_composite_rows)
