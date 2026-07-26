"""Agreement statistics for judge-vs-human validation (V-053, TESTING §2).

Pure arithmetic — no DB, no LLM, no network, no third-party dependency
(stdlib `math` only, so this adds nothing to the free-tier footprint and
every number is reproducible by hand at the defense).

WHY THIS MODULE EXISTS. The harness used to report a bare agreement
percentage. Against a hostile panel that is indefensible for two reasons the
literature is explicit about:

1. **Accuracy alone misleads on imbalanced criteria** (the "kappa paradox").
   A judge that always answers "pass" scores 90% accuracy on a criterion 90%
   of manuscripts satisfy, while carrying zero information. Chance-corrected
   agreement exposes that; raw accuracy hides it. So accuracy is never
   reported here without Cohen's kappa beside it.
2. **A percentage from a small sample is not an estimate, it is an anecdote.**
   The first real baseline run produced "agreement 1.0" from ONE graded item.
   Its actual 95% Wilson interval is [0.21, 1.00] — i.e. the data is
   consistent with the system being right 21% of the time. Every rate this
   module reports therefore carries an interval.

Method choices, and why (see context/RESEARCH.md §9 for sources):
- **Wilson score interval**, not the normal-approximation (Wald) interval.
  Wald assumes asymptotic normality, needs roughly n>=30, and collapses to a
  zero-width interval at p=0 or p=1 — exactly our regime. Wilson is the
  recommended small-sample default and stays sane from about n=10.
- **Cohen's kappa AND Gwet's AC1.** Kappa is the standard chance-corrected
  coefficient but behaves paradoxically when one class dominates; AC1 is
  paradox-resistant. They disagree precisely when prevalence is extreme,
  which is a fact worth SEEING, so both are reported with the prevalence
  that drives them. (The Landis-Koch verbal bands are for kappa; they are
  not transferable to AC1, so this module labels neither.)
- **MCC (= the phi coefficient) only.** On binary verdicts Pearson's r,
  Spearman's rho, Kendall's tau-b, phi and MCC are the SAME statistic;
  reporting several would manufacture false triangulation.
- **A confusion matrix**, because "70% accurate" does not say whether the
  judge is too harsh or too lenient — and for VERIDICAL those two errors are
  not equivalent. A false "fail" accuses a student wrongly; that is the
  expensive direction (charter: precision beats recall).
"""

import math
from dataclasses import dataclass

# 95% two-sided normal quantile. Not a tunable knob — changing it changes
# what "95%" means — so it is a named constant, not config.
Z_95 = 1.959963984540054


@dataclass(frozen=True)
class Interval:
    low: float
    high: float

    def as_percent(self) -> str:
        return f"[{self.low:.1%}, {self.high:.1%}]"


def wilson_interval(successes: int, n: int, z: float = Z_95) -> Interval | None:
    """Wilson score interval for a binomial proportion.

    Returns None for n == 0 — an interval over no observations is not a wide
    interval, it is no information at all, and the two must not look alike.
    """
    if n <= 0:
        return None
    if successes < 0 or successes > n:
        raise ValueError(f"successes={successes} out of range for n={n}")
    p = successes / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denominator
    margin = (z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / denominator
    return Interval(low=max(0.0, center - margin), high=min(1.0, center + margin))


@dataclass(frozen=True)
class ConfusionMatrix:
    """Binary confusion of judge vs human, with the HUMAN as ground truth.

    `positive` = the "pass" verdict. tp/fn/fp/tn follow the usual meaning:
    e.g. `fp` is the judge saying pass where the instructor said fail.
    """

    tp: int
    fp: int
    tn: int
    fn: int

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    @property
    def observed_agreement(self) -> float | None:
        return (self.tp + self.tn) / self.n if self.n else None

    @property
    def human_positive_rate(self) -> float | None:
        """Prevalence — the share of items the INSTRUCTOR passed. This is
        what drives the kappa paradox, so it is always reported alongside."""
        return (self.tp + self.fn) / self.n if self.n else None

    @property
    def judge_positive_rate(self) -> float | None:
        return (self.tp + self.fp) / self.n if self.n else None

    @property
    def leniency(self) -> float | None:
        """judge_positive_rate - human_positive_rate. Positive = the judge
        passes more than the instructor does (too lenient); negative = it
        fails more (too harsh — the expensive direction for this product,
        because a wrong 'fail' is a wrong accusation)."""
        judge, human = self.judge_positive_rate, self.human_positive_rate
        return None if judge is None or human is None else judge - human


def cohens_kappa(matrix: ConfusionMatrix) -> float | None:
    """Chance-corrected agreement. None when undefined (no data, or perfect
    chance agreement — which happens when one rater is completely constant
    and the correction has nothing to work with)."""
    n = matrix.n
    if n == 0:
        return None
    po = (matrix.tp + matrix.tn) / n
    p_yes = ((matrix.tp + matrix.fn) / n) * ((matrix.tp + matrix.fp) / n)
    p_no = ((matrix.tn + matrix.fp) / n) * ((matrix.tn + matrix.fn) / n)
    pe = p_yes + p_no
    if math.isclose(pe, 1.0):
        return None
    return (po - pe) / (1 - pe)


def gwet_ac1(matrix: ConfusionMatrix) -> float | None:
    """Gwet's AC1 — chance-corrected like kappa, but its chance term uses
    the mean marginal rather than the product of marginals, which is what
    makes it resistant to the high-prevalence paradox."""
    n = matrix.n
    if n == 0:
        return None
    po = (matrix.tp + matrix.tn) / n
    human = (matrix.tp + matrix.fn) / n
    judge = (matrix.tp + matrix.fp) / n
    pi = (human + judge) / 2
    pe = 2 * pi * (1 - pi)
    if math.isclose(pe, 1.0):
        return None
    return (po - pe) / (1 - pe)


def matthews_corrcoef(matrix: ConfusionMatrix) -> float | None:
    """MCC, identical to the phi coefficient on binary data. None when a
    margin is degenerate (a row or column of the matrix is all zero), which
    is a real state — 'undefined', not 'zero correlation'."""
    tp, fp, tn, fn = matrix.tp, matrix.fp, matrix.tn, matrix.fn
    denominator = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    if denominator == 0:
        return None
    return (tp * tn - fp * fn) / denominator


@dataclass(frozen=True)
class AgreementStats:
    """Everything the panel should be shown about one judge-vs-human
    comparison, in one object."""

    matrix: ConfusionMatrix
    accuracy: float | None
    accuracy_ci: Interval | None
    kappa: float | None
    ac1: float | None
    mcc: float | None
    # Abstention accounting. The harness runs in EXCLUDE mode: escalated
    # items are withheld rather than scored, so accuracy above is SELECTIVE
    # accuracy over the covered subset and means nothing without coverage.
    n_abstained: int
    coverage: float | None

    @property
    def is_degenerate(self) -> bool:
        """True when every item shares one label, so chance-corrected
        coefficients cannot be computed. Reported as N/A — never dropped
        silently, never rendered as 0."""
        return self.kappa is None or self.mcc is None


def compute_agreement(
    *,
    human_labels: list[str],
    judge_labels: list[str],
    positive: str = "pass",
    n_abstained: int = 0,
    z: float = Z_95,
) -> AgreementStats:
    """Build every statistic from paired labels of the COVERED items only.

    `n_abstained` counts items the judge declined to decide; they are not
    passed in as labels because there is no judgement to compare, but they
    are what `coverage` is computed from — an accuracy figure achieved by
    abstaining on everything hard is not a good result, and hiding the
    abstention rate would make it look like one.
    """
    if len(human_labels) != len(judge_labels):
        raise ValueError("human_labels and judge_labels must be the same length")

    tp = fp = tn = fn = 0
    for human, judge in zip(human_labels, judge_labels, strict=True):
        human_positive = human == positive
        judge_positive = judge == positive
        if human_positive and judge_positive:
            tp += 1
        elif not human_positive and judge_positive:
            fp += 1
        elif not human_positive and not judge_positive:
            tn += 1
        else:
            fn += 1

    matrix = ConfusionMatrix(tp=tp, fp=fp, tn=tn, fn=fn)
    n_covered = matrix.n
    n_offered = n_covered + n_abstained
    return AgreementStats(
        matrix=matrix,
        accuracy=matrix.observed_agreement,
        accuracy_ci=wilson_interval(tp + tn, n_covered, z) if n_covered else None,
        kappa=cohens_kappa(matrix),
        ac1=gwet_ac1(matrix),
        mcc=matthews_corrcoef(matrix),
        n_abstained=n_abstained,
        coverage=(n_covered / n_offered) if n_offered else None,
    )
