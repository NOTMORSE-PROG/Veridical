"""statcheck p-value recomputation (F6.4, V-033) — wraps
`statcheck_python`'s own `compute_p`/`error_test`/`decision_error_test`
directly (D-003), applied to V-031's already-
extracted `ReportedStat` records rather than re-extracting from raw text
a second time (statcheck's own `extract_stats` already ran once, in
V-031's `extract_inferential_stats`).

The orchestration preserves reported degrees of freedom; the upstream
`process_stats` wrapper rounds them to integers. One-tailed mercy retains
the upstream rule: a permitted one-tailed interpretation can resolve an
otherwise inconsistent two-tailed result.
"""

import contextlib
import io
import math
import warnings
from dataclasses import dataclass

from statcheck.compute_p import compute_p
from statcheck.decision_error_test import decision_error_test
from statcheck.error_test import error_test
from statcheck.extract_1tail import extract_1tail

from app.checks.forensics.checks import ForensicsFlagDraft
from app.checks.forensics.extract import ReportedStat
from app.models.enums import FlagSeverity

PVALUE_MISMATCH_WORDING = (
    "The reported p-value ({reported_p}) differs from the recalculated "
    "value ({computed_p}) for this {test_type} test. Possible rounding "
    "error or typo."
)
PVALUE_DECISION_ERROR_WORDING = (
    "The reported p-value ({reported_p}) differs from the recalculated "
    "value ({computed_p}) for this {test_type} test, AND the difference "
    "changes whether the result would be called statistically significant. "
    "Possible rounding error or typo, please verify."
)

_SUPPORTED_TYPES = {"t", "F", "r", "Chi2", "Z", "Q"}
_SIGNED_TYPES = {"t", "r", "Z"}


@dataclass(frozen=True)
class PRecalcResult:
    computed_p: float
    error: bool
    decision_error: bool


def _evaluate_tail(
    stat: ReportedStat,
    test_value: float,
    *,
    two_tailed: bool,
    alpha: float,
    p_zero_error: bool,
    p_equal_alpha_sig: bool,
) -> PRecalcResult | None:
    """Reuse library judgments with the source df, without integer coercion."""
    distribution = {
        "test_type": stat.test_type,
        "test_stat": test_value,
        "df1": stat.df1,
        "df2": stat.df2,
        "two_tailed": two_tailed,
    }
    computed_p = float(compute_p(**distribution))
    if not math.isfinite(computed_p):
        return None
    comparisons = {
        "reported_p": stat.p_value,
        "p_comparison": stat.p_comparison or "=",
        "test_comparison": stat.test_comparison or "=",
        "alpha": alpha,
    }
    error = error_test(
        **distribution,
        **comparisons,
        p_dec=stat.p_value_precision,
        test_dec=stat.test_value_precision,
        pZeroError=p_zero_error,
    )
    if error is None:
        return None
    decision_error = (
        decision_error_test(**comparisons, computed_p=computed_p, pEqualAlphaSig=p_equal_alpha_sig)
        if error
        else False
    )
    if decision_error is None:
        return None
    return PRecalcResult(computed_p, bool(error), bool(decision_error))


def recompute_p(
    stat: ReportedStat,
    *,
    one_tailed_in_text: bool,
    alpha: float = 0.05,
    p_zero_error: bool = True,
    p_equal_alpha_sig: bool = True,
    two_tailed: bool = True,
) -> PRecalcResult | None:
    """None means "not applicable" — missing fields, or a test type this
    module doesn't recompute (never guessed, ticket AC #3's own
    discipline applied here too)."""
    if stat.kind != "inferential" or stat.test_type not in _SUPPORTED_TYPES:
        return None
    if stat.test_value is None or stat.p_value is None:
        return None
    if stat.test_value_precision is None or stat.p_value_precision is None:
        return None

    # statcheck_python builds the rounding interval as value +/- half a unit
    # and then reads "up" and "low" p from its two ends. For a NEGATIVE
    # statistic the ends are swapped, so every correctly reported negative
    # t/z/r became a mismatch (measured 2026-10-07: t(68) = -1.67, p = .100
    # flagged although p = .0995). These tests are two-tailed and symmetric
    # in sign, so the magnitude is the statistic statcheck must see.
    test_value = abs(stat.test_value) if stat.test_type in _SIGNED_TYPES else stat.test_value
    try:
        with contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
            # Same defensive stdout suppression as V-031's extraction —
            # statcheck's internals print on some failure paths.
            warnings.simplefilter("ignore")
            result = _evaluate_tail(
                stat,
                test_value,
                two_tailed=two_tailed,
                alpha=alpha,
                p_zero_error=p_zero_error,
                p_equal_alpha_sig=p_equal_alpha_sig,
            )
            if result is not None and result.error and one_tailed_in_text and two_tailed:
                alternative = _evaluate_tail(
                    stat,
                    test_value,
                    two_tailed=False,
                    alpha=alpha,
                    p_zero_error=p_zero_error,
                    p_equal_alpha_sig=p_equal_alpha_sig,
                )
                if alternative is None:
                    return None
                if not alternative.error:
                    result = alternative
    except Exception:  # noqa: BLE001 — never crash on a manuscript's own weird numbers
        return None

    return result


def evaluate_p_recalc(
    stats: list[ReportedStat],
    full_text: str,
    *,
    tail_context_complete: bool = True,
    unassessed_stats: list[ReportedStat] | None = None,
) -> list[ForensicsFlagDraft]:
    try:
        one_tailed_in_text = bool(extract_1tail(full_text))
    except Exception:  # noqa: BLE001 — mercy detection failing must never block the check
        one_tailed_in_text = False

    flags: list[ForensicsFlagDraft] = []
    for stat in stats:
        result = recompute_p(stat, one_tailed_in_text=one_tailed_in_text)
        if result is None:
            if unassessed_stats is not None:
                unassessed_stats.append(stat)
            continue
        if not result.error or not stat.raw_text:
            continue
        alternative = None
        if not tail_context_complete:
            # Excluded source may contain the test-tail declaration. A mismatch
            # must survive both interpretations; absence of the phrase in a
            # shortened text cannot establish that it was a two-tailed test.
            alternative = recompute_p(stat, one_tailed_in_text=False, two_tailed=False)
            if alternative is None or not alternative.error:
                continue
        decision_error = result.decision_error and (
            alternative is None or alternative.decision_error
        )
        p_prec = stat.p_value_precision or 3
        reported_str = f"{stat.p_comparison or '='} {stat.p_value:.{p_prec}f}"
        computed_str = f"{result.computed_p:.{p_prec}f}"
        wording = PVALUE_DECISION_ERROR_WORDING if decision_error else PVALUE_MISMATCH_WORDING
        reason = wording.format(
            reported_p=reported_str, computed_p=computed_str, test_type=stat.test_type
        )
        if alternative is not None:
            reason += (
                f" The one-tailed recalculation ({alternative.computed_p:.{p_prec}f}) "
                "also differs. "
                "Some source content was excluded, so the manuscript's test-tail description "
                "could not be fully checked."
            )
        flags.append(
            ForensicsFlagDraft(
                severity=FlagSeverity.high if decision_error else FlagSeverity.med,
                evidence_excerpt=stat.raw_text,
                page_anchor=stat.anchor,
                detail={
                    "kind": "p_value_decision_error" if decision_error else "p_value_mismatch",
                    "reason": reason,
                    "reported_p": stat.p_value,
                    "computed_p": result.computed_p,
                    "reported_p_display": reported_str,
                    "computed_p_display": f"≈ {computed_str}",
                    "test_type": stat.test_type,
                    "test_value_display": f"{stat.test_value:.{stat.test_value_precision}f}",
                    "df1": stat.df1,
                    "df2": stat.df2,
                    "one_tailed_in_text": one_tailed_in_text,
                    **(
                        {
                            "tail_context_complete": False,
                            "one_tailed_computed_p": alternative.computed_p,
                        }
                        if alternative is not None
                        else {}
                    ),
                },
            )
        )
    return flags
