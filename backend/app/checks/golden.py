"""Golden-dataset scoring (V-025, TESTING.md §2): PURE aggregation over
already-graded rows — no DB, no LLM, no network (same design principle as
`app.report.scoring`: the arithmetic is unit-testable in isolation from
any real call, and callers project their own data into `GoldenRow`).

An escalated item is a NON-ANSWER, not a wrong answer (charter rule 1) —
it is excluded from agreement (which would otherwise conflate "the
system was appropriately unsure" with "the system was wrong") and
reported separately as this set's escalation rate (D-012 mechanism #2).
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

Verdict = Literal["pass", "fail"]


@dataclass(frozen=True)
class GoldenItem:
    """One row of `context/golden/set.jsonl`."""

    id: str
    criterion_text: str
    criterion_type: str
    excerpt: str
    instructor_grade: Verdict
    reason: str
    source: str


@dataclass(frozen=True)
class GoldenPrediction:
    """One item's graded outcome, projected from whatever produced it
    (the live pipeline via `app.checks.consistency.vote_batch`, or a
    test double) into the shape this module needs.

    `error` (api_down/quota_exhausted, TESTING.md §5) is distinct from
    `escalated`: an escalation is the SYSTEM correctly saying "I'm not
    sure"; an error is the system never getting to answer at all —
    conflating them would misreport an outage as a confidence problem
    (same failure-taxonomy discipline as every other check in this
    project)."""

    item: GoldenItem
    escalated: bool
    predicted: Verdict | None  # None when escalated OR errored
    agreement: float | None
    votes: list[str | None]
    error: str | None = None


@dataclass(frozen=True)
class ClassBreakdown:
    criterion_text: str
    n: int
    n_escalated: int
    n_agree: int
    n_decided: int
    agreement_rate: float | None  # None if n_decided == 0


@dataclass(frozen=True)
class GoldenReport:
    n_total: int
    n_errored: int
    n_escalated: int
    escalation_rate: float
    n_decided: int
    n_agree: int
    agreement_rate: float | None  # overall, decided-only (None if n_decided == 0)
    by_criterion: list[ClassBreakdown]
    disagreements: list[GoldenPrediction]
    errored: list[GoldenPrediction]


def score_golden_set(predictions: list[GoldenPrediction]) -> GoldenReport:
    n_total = len(predictions)
    errored = [p for p in predictions if p.error is not None]
    gradeable = [p for p in predictions if p.error is None]
    n_escalated = sum(1 for p in gradeable if p.escalated)
    decided = [p for p in gradeable if not p.escalated]
    n_decided = len(decided)
    agreeing = [p for p in decided if p.predicted == p.item.instructor_grade]
    n_agree = len(agreeing)

    by_criterion_preds: dict[str, list[GoldenPrediction]] = defaultdict(list)
    for p in gradeable:
        by_criterion_preds[p.item.criterion_text].append(p)

    by_criterion = []
    for criterion_text, preds in by_criterion_preds.items():
        c_decided = [p for p in preds if not p.escalated]
        c_agree = [p for p in c_decided if p.predicted == p.item.instructor_grade]
        by_criterion.append(
            ClassBreakdown(
                criterion_text=criterion_text,
                n=len(preds),
                n_escalated=sum(1 for p in preds if p.escalated),
                n_agree=len(c_agree),
                n_decided=len(c_decided),
                agreement_rate=(len(c_agree) / len(c_decided)) if c_decided else None,
            )
        )

    disagreements = [
        p for p in decided if p.predicted != p.item.instructor_grade
    ]

    return GoldenReport(
        n_total=n_total,
        n_errored=len(errored),
        n_escalated=n_escalated,
        escalation_rate=(n_escalated / n_total) if n_total else 0.0,
        n_decided=n_decided,
        n_agree=n_agree,
        agreement_rate=(n_agree / n_decided) if n_decided else None,
        by_criterion=by_criterion,
        disagreements=disagreements,
        errored=errored,
    )


def report_as_markdown(report: GoldenReport, *, title: str, provisional_note: str) -> str:
    lines = [f"# {title}", "", provisional_note, ""]
    lines.append(f"- Items: {report.n_total}")
    if report.n_errored:
        lines.append(
            f"- ⚠️ api_down/quota_exhausted (never counted as escalated OR wrong, "
            f"TESTING.md §5): {report.n_errored} — run incomplete, re-run when the "
            f"underlying cause clears"
        )
    lines.append(
        f"- Escalated (excluded from agreement, D-012 #2): {report.n_escalated} "
        f"({report.escalation_rate:.1%})"
    )
    if report.agreement_rate is None:
        lines.append("- Agreement: N/A — every item escalated, nothing decided.")
    else:
        lines.append(
            f"- Agreement (decided items only): {report.n_agree}/{report.n_decided} "
            f"({report.agreement_rate:.1%})"
        )
    lines.append("")
    lines.append("## Per-criterion breakdown")
    for c in report.by_criterion:
        rate = f"{c.agreement_rate:.1%}" if c.agreement_rate is not None else "N/A"
        lines.append(
            f'- "{c.criterion_text[:70]}" — {c.n_agree}/{c.n_decided} ({rate}), '
            f"{c.n_escalated} escalated, n={c.n}"
        )
    lines.append("")
    lines.append(f"## Disagreements ({len(report.disagreements)})")
    if not report.disagreements:
        lines.append("None.")
    for d in report.disagreements:
        lines.append(
            f"- `{d.item.id}` predicted **{d.predicted}**, instructor graded "
            f'**{d.item.instructor_grade}** — "{d.item.criterion_text[:70]}" '
            f"(agreement {d.agreement}, votes {d.votes})"
        )
    if report.errored:
        lines.append("")
        lines.append(f"## Errored — never graded ({len(report.errored)})")
        for e in report.errored:
            lines.append(f"- `{e.item.id}`: {e.error}")
    return "\n".join(lines)
