"""Agreement between automatic rubric-level grades and an instructor's own
grades of the same criteria (TESTING.md §2 and §6, D-016).

Pure arithmetic over already-collected rows: no DB, no model calls. The
caller loads the instructor's label sheet and the automatic grades and
projects them into `LabelledDecision`.

Two rules shape everything here:

* Only an instructor's grade is ground truth. A sheet whose provenance says
  the grades came from a model, or that does not say who graded, is refused
  outright rather than scored, because an accuracy figure computed against
  machine-made "labels" measures nothing and would still look like evidence.
* An escalated criterion (the system left it to the instructor) is not a
  wrong answer and not a right one. It is kept out of the agreement
  denominator and reported as the escalation rate, with an end-to-end figure
  beside it that counts escalations as misses, so a high agreement bought by
  escalating everything hard is visible.
"""

import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from app.checks.accuracy_stats import Interval, wilson_interval

# Values a sheet may carry in `label_source` for a row to count.
INSTRUCTOR_SOURCE = "instructor"

# Names and sources that mean the grade did not come from a person. Matched
# as whole words, case-insensitively, against `labelled_by` and
# `label_source`.
_MACHINE_MARKERS = (
    "ai",
    "llm",
    "gpt",
    "chatgpt",
    "gemini",
    "copilot",
    "model",
    "bot",
    "agent",
    "auto",
    "automatic",
    "generated",
    "veridical",
    "synthetic",
    "machine",
)
_MACHINE_RE = re.compile(r"\b(" + "|".join(_MACHINE_MARKERS) + r")\b", re.IGNORECASE)


class ProvenanceError(ValueError):
    """The label sheet cannot be trusted as instructor ground truth."""


@dataclass(frozen=True)
class InstructorLabel:
    row_id: str
    level: int | None  # None when the instructor could not judge
    cannot_judge: bool
    labelled_by: str
    label_source: str
    ai_revealed_before_label: bool | None = None
    rationale: str = ""


@dataclass(frozen=True)
class AutomaticGrade:
    row_id: str
    manuscript: str
    criterion: str
    decided: bool  # False when the criterion was escalated to the instructor
    level: int | None
    flagged: bool = False  # e.g. graded against a misparsed scale


@dataclass(frozen=True)
class LabelledDecision:
    grade: AutomaticGrade
    label: InstructorLabel


def check_provenance(labels: Iterable[InstructorLabel]) -> list[InstructorLabel]:
    """Return the labels that count, or refuse the sheet.

    A row nobody graded (no level, not marked cannot-judge) is simply not a
    label and is skipped. Every graded row must name a person and carry the
    instructor source; any row that does not, or that names a model, makes
    the whole sheet suspect, so the whole sheet is refused.
    """
    counted = []
    problems = []
    for label in labels:
        graded = label.level is not None or label.cannot_judge
        source = (label.label_source or "").strip()
        who = (label.labelled_by or "").strip()
        if not graded:
            if source and _MACHINE_RE.search(source):
                problems.append(f"{label.row_id}: label_source '{source}' is not an instructor")
            continue
        if source.lower() != INSTRUCTOR_SOURCE:
            problems.append(f"{label.row_id}: label_source is '{source}', expected 'instructor'")
        if not who:
            problems.append(f"{label.row_id}: labelled_by is empty")
        elif _MACHINE_RE.search(who):
            problems.append(f"{label.row_id}: labelled_by '{who}' does not name a person")
        counted.append(label)
    if problems:
        raise ProvenanceError(
            "This sheet cannot be scored as instructor ground truth:\n  " + "\n  ".join(problems)
        )
    return counted


def cohens_kappa_nominal(pairs: Sequence[tuple[int, int]]) -> float | None:
    """Unweighted Cohen's kappa over any number of categories. None when it
    is undefined (no pairs, or chance agreement is already total because
    both raters used a single shared category)."""
    n = len(pairs)
    if n == 0:
        return None
    categories = sorted({c for pair in pairs for c in pair})
    po = sum(1 for a, b in pairs if a == b) / n
    pe = sum(
        (sum(1 for a, _ in pairs if a == c) / n) * (sum(1 for _, b in pairs if b == c) / n)
        for c in categories
    )
    if math.isclose(pe, 1.0):
        return None
    return (po - pe) / (1 - pe)


def weighted_kappa(pairs: Sequence[tuple[int, int]], *, scale: Sequence[int]) -> float | None:
    """Quadratic-weighted kappa on the criterion's ordinal scale: a
    two-level miss costs four times a one-level miss. None when undefined."""
    n = len(pairs)
    if n == 0 or len(scale) < 2:
        return None
    span = (max(scale) - min(scale)) ** 2
    rows = {c: sum(1 for a, _ in pairs if a == c) / n for c in scale}
    cols = {c: sum(1 for _, b in pairs if b == c) / n for c in scale}
    observed = sum((a - b) ** 2 / span for a, b in pairs) / n
    expected = sum(rows[i] * cols[j] * (i - j) ** 2 / span for i in scale for j in scale)
    if math.isclose(expected, 0.0):
        return None
    return 1 - observed / expected


@dataclass(frozen=True)
class AgreementSlice:
    name: str
    n_offered: int  # rows with an instructor label (cannot-judge excluded)
    n_escalated: int
    n_decided: int
    n_exact: int
    n_within_one: int
    exact: float | None
    exact_ci: Interval | None
    within_one: float | None
    within_one_ci: Interval | None
    end_to_end_exact: float | None  # escalations counted as misses
    end_to_end_ci: Interval | None
    kappa: float | None
    weighted_kappa: float | None
    mean_signed_difference: float | None  # automatic minus instructor; >0 = lenient


def _slice(
    name: str, decisions: Sequence[LabelledDecision], scale: Sequence[int]
) -> AgreementSlice:
    offered = [d for d in decisions if d.label.level is not None]
    decided = [d for d in offered if d.grade.decided and d.grade.level is not None]
    pairs = [(d.grade.level, d.label.level) for d in decided]
    n_dec = len(pairs)
    n_exact = sum(1 for a, b in pairs if a == b)
    n_within = sum(1 for a, b in pairs if abs(a - b) <= 1)
    n_off = len(offered)
    return AgreementSlice(
        name=name,
        n_offered=n_off,
        n_escalated=n_off - n_dec,
        n_decided=n_dec,
        n_exact=n_exact,
        n_within_one=n_within,
        exact=n_exact / n_dec if n_dec else None,
        exact_ci=wilson_interval(n_exact, n_dec),
        within_one=n_within / n_dec if n_dec else None,
        within_one_ci=wilson_interval(n_within, n_dec),
        end_to_end_exact=n_exact / n_off if n_off else None,
        end_to_end_ci=wilson_interval(n_exact, n_off),
        kappa=cohens_kappa_nominal(pairs),
        weighted_kappa=weighted_kappa(pairs, scale=scale),
        mean_signed_difference=(sum(a - b for a, b in pairs) / n_dec) if n_dec else None,
    )


@dataclass(frozen=True)
class GateResult:
    """D-016: the 95% Wilson lower bound of exact agreement must reach the
    threshold on at least `min_n` decided rows."""

    status: str  # "pass" | "fail" | "insufficient"
    lower_bound: float | None
    threshold: float
    n_decided: int
    min_n: int


def evaluate_gate(slice_: AgreementSlice, *, threshold: float, min_n: int) -> GateResult:
    low = slice_.exact_ci.low if slice_.exact_ci else None
    if slice_.n_decided < min_n:
        status = "insufficient"
    else:
        status = "pass" if low is not None and low >= threshold else "fail"
    return GateResult(status, low, threshold, slice_.n_decided, min_n)


@dataclass(frozen=True)
class RubricAgreementReport:
    n_rows: int
    n_labelled: int
    n_cannot_judge: int
    escalation_rate: float | None  # escalated / labelled rows
    overall: AgreementSlice
    unflagged: AgreementSlice
    blind: AgreementSlice | None
    gate: GateResult
    gate_unflagged: GateResult
    by_criterion: list[AgreementSlice]
    by_manuscript: list[AgreementSlice]
    disagreements: list[LabelledDecision] = field(default_factory=list)
    unlabelled_row_ids: list[str] = field(default_factory=list)


def score(
    grades: Sequence[AutomaticGrade],
    labels: Sequence[InstructorLabel],
    *,
    scale: Sequence[int],
    threshold: float,
    min_n: int,
) -> RubricAgreementReport:
    counted = {label.row_id: label for label in check_provenance(labels)}
    off_scale = sorted(
        r for r, label in counted.items() if label.level is not None and label.level not in scale
    )
    if off_scale:
        raise ValueError(f"Instructor levels outside the criterion scale: {', '.join(off_scale)}")
    unknown = sorted(set(counted) - {g.row_id for g in grades})
    if unknown:
        raise ValueError(f"Labels for rows that have no automatic grade: {', '.join(unknown)}")
    joined = [LabelledDecision(g, counted[g.row_id]) for g in grades if g.row_id in counted]
    judged = [d for d in joined if d.label.level is not None]

    def group(key: Callable[[LabelledDecision], str]) -> list[AgreementSlice]:
        buckets: dict[str, list[LabelledDecision]] = defaultdict(list)
        for d in joined:
            buckets[key(d)].append(d)
        return [_slice(name, items, scale) for name, items in buckets.items()]

    overall = _slice("all rows", joined, scale)
    unflagged = _slice(
        "excluding flagged criteria", [d for d in joined if not d.grade.flagged], scale
    )
    has_blind_info = any(d.label.ai_revealed_before_label is not None for d in judged)
    blind = (
        _slice(
            "graded before seeing the automatic grade",
            [d for d in joined if d.label.ai_revealed_before_label is False],
            scale,
        )
        if has_blind_info
        else None
    )
    return RubricAgreementReport(
        n_rows=len(grades),
        n_labelled=len(judged),
        n_cannot_judge=sum(1 for d in joined if d.label.cannot_judge),
        escalation_rate=(overall.n_escalated / overall.n_offered) if overall.n_offered else None,
        overall=overall,
        unflagged=unflagged,
        blind=blind,
        gate=evaluate_gate(overall, threshold=threshold, min_n=min_n),
        gate_unflagged=evaluate_gate(unflagged, threshold=threshold, min_n=min_n),
        by_criterion=group(lambda d: d.grade.criterion),
        by_manuscript=group(lambda d: d.grade.manuscript),
        disagreements=[
            d
            for d in judged
            if d.grade.decided and d.grade.level is not None and d.grade.level != d.label.level
        ],
        unlabelled_row_ids=[g.row_id for g in grades if g.row_id not in counted],
    )


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1%}"


def _ci(interval: Interval | None) -> str:
    return "N/A" if interval is None else interval.as_percent()


def _num(value: float | None) -> str:
    return "N/A (undefined on these rows)" if value is None else f"{value:.3f}"


def _slice_line(s: AgreementSlice) -> str:
    return (
        f"| {s.name[:60]} | {s.n_offered} | {s.n_escalated} | {s.n_decided} | "
        f"{s.n_exact}/{s.n_decided} {_pct(s.exact)} {_ci(s.exact_ci)} | "
        f"{s.n_within_one}/{s.n_decided} {_pct(s.within_one)} | {_num(s.kappa)} |"
    )


def report_as_markdown(report: RubricAgreementReport, *, level_names: dict[int, str]) -> str:
    def gate_line(label: str, gate: GateResult) -> str:
        if gate.status == "insufficient":
            return (
                f"- **{label}: NOT EVALUABLE.** {gate.n_decided} decided rows; the gate needs at "
                f"least {gate.min_n} (a {gate.threshold:.0%} lower bound is unreachable below that "
                f"even at perfect agreement)."
            )
        word = "PASS" if gate.status == "pass" else "FAIL"
        return (
            f"- **{label}: {word}.** Wilson 95% lower bound {_pct(gate.lower_bound)} vs "
            f"{gate.threshold:.0%} required, on {gate.n_decided} decided rows."
        )

    o = report.overall
    lines = [
        "# Rubric grading agreement with the instructor",
        "",
        f"- Rows offered: {report.n_rows}; graded by the instructor: {report.n_labelled}; "
        f"cannot judge: {report.n_cannot_judge}; not yet graded: {len(report.unlabelled_row_ids)}.",
        f"- Escalated to the instructor (no automatic grade): {o.n_escalated} of {o.n_offered} "
        f"({_pct(report.escalation_rate)}). Escalations are not counted in exact agreement.",
        "",
        "## D-016 gate (exact level agreement)",
        gate_line("All criteria", report.gate),
        gate_line("Excluding flagged criteria", report.gate_unflagged),
        "",
        "## Headline figures",
    ]
    for s in [o, report.unflagged] + ([report.blind] if report.blind else []):
        lines += [
            f"### {s.name}",
            f"- Exact level agreement: {s.n_exact}/{s.n_decided} = {_pct(s.exact)}, "
            f"95% CI (Wilson) {_ci(s.exact_ci)}",
            f"- Within one level: {s.n_within_one}/{s.n_decided} = {_pct(s.within_one)}, "
            f"95% CI {_ci(s.within_one_ci)}",
            f"- End to end (escalations count as misses): {s.n_exact}/{s.n_offered} = "
            f"{_pct(s.end_to_end_exact)}, 95% CI {_ci(s.end_to_end_ci)}",
            f"- Cohen's kappa: {_num(s.kappa)}; quadratic-weighted kappa: {_num(s.weighted_kappa)}",
            "- Bias (automatic minus instructor, in levels): "
            + ("N/A" if s.mean_signed_difference is None else f"{s.mean_signed_difference:+.2f}"),
            "",
        ]
    header = "| Group | Graded | Escalated | Decided | Exact (95% CI) | Within one | Kappa |"
    rule = "|---|---|---|---|---|---|---|"
    lines += ["## By criterion", header, rule, *[_slice_line(s) for s in report.by_criterion], ""]
    lines += ["## By manuscript", header, rule, *[_slice_line(s) for s in report.by_manuscript], ""]
    lines.append(f"## Disagreements ({len(report.disagreements)})")
    for d in report.disagreements:
        lines.append(
            f"- `{d.grade.row_id}` {d.grade.criterion[:50]} ({d.grade.manuscript}): automatic "
            f"**{level_names.get(d.grade.level, d.grade.level)}**, instructor "
            f"**{level_names.get(d.label.level, d.label.level)}**"
            + (" (flagged criterion)" if d.grade.flagged else "")
            + (f". Instructor's reason: {d.label.rationale}" if d.label.rationale else "")
        )
    if not report.disagreements:
        lines.append("None.")
    return "\n".join(lines) + "\n"
