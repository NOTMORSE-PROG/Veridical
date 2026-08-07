"""statcheck p-value recomputation (F6.4, V-033) — wraps
`statcheck_python`'s own `process_stats`/`compute_p`/`error_test`/
`decision_error_test` directly (D-003), applied to V-031's already-
extracted `ReportedStat` records rather than re-extracting from raw text
a second time (statcheck's own `extract_stats` already ran once, in
V-031's `extract_inferential_stats`).

One-tailed mercy (ticket edge case: "don't flag if one-tailed would make
it consistent — inherit that mercy") is `process_stats`'s own built-in
behavior when `OneTailedTxt=True` — not reimplemented here, just enabled.
"""

import contextlib
import io
import warnings
from dataclasses import dataclass

from statcheck.extract_1tail import extract_1tail
from statcheck.process_stats import process_stats

from app.checks.forensics.checks import ForensicsFlagDraft
from app.checks.forensics.extract import ReportedStat
from app.models.enums import FlagSeverity

PVALUE_MISMATCH_WORDING = (
    "The reported p-value ({reported_p}) differs from the recalculated "
    "value ({computed_p}) for this {test_type} test — possible rounding "
    "error or typo."
)
PVALUE_DECISION_ERROR_WORDING = (
    "The reported p-value ({reported_p}) differs from the recalculated "
    "value ({computed_p}) for this {test_type} test, AND the difference "
    "changes whether the result would be called statistically significant "
    "— possible rounding error or typo, please verify."
)

_SUPPORTED_TYPES = {"t", "F", "r", "Chi2", "Z", "Q"}


@dataclass(frozen=True)
class PRecalcResult:
    computed_p: float
    error: bool
    decision_error: bool


def recompute_p(
    stat: ReportedStat,
    *,
    one_tailed_in_text: bool,
    alpha: float = 0.05,
    p_zero_error: bool = True,
    p_equal_alpha_sig: bool = True,
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

    try:
        with contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
            # Same defensive stdout suppression as V-031's extraction —
            # statcheck's internals print on some failure paths.
            warnings.simplefilter("ignore")
            df = process_stats(
                stat.test_type,
                test_stat=stat.test_value,
                df1=stat.df1,
                df2=stat.df2,
                reported_p=stat.p_value,
                p_comparison=stat.p_comparison or "=",
                test_comparison=stat.test_comparison or "=",
                p_dec=stat.p_value_precision,
                test_dec=stat.test_value_precision,
                OneTailedInTxt=one_tailed_in_text,
                two_tailed=True,
                alpha=alpha,
                pZeroError=p_zero_error,
                pEqualAlphaSig=p_equal_alpha_sig,
                OneTailedTxt=True,
                OneTailedTests=False,
            )
    except Exception:  # noqa: BLE001 — never crash on a manuscript's own weird numbers
        return None

    row = df.iloc[0]
    return PRecalcResult(
        computed_p=float(row["computed_p"]),
        error=bool(row["error"]),
        decision_error=bool(row["decision_error"]),
    )


def evaluate_p_recalc(stats: list[ReportedStat], full_text: str) -> list[ForensicsFlagDraft]:
    try:
        one_tailed_in_text = bool(extract_1tail(full_text))
    except Exception:  # noqa: BLE001 — mercy detection failing must never block the check
        one_tailed_in_text = False

    flags: list[ForensicsFlagDraft] = []
    for stat in stats:
        result = recompute_p(stat, one_tailed_in_text=one_tailed_in_text)
        if result is None or not result.error:
            continue
        p_prec = stat.p_value_precision or 3
        reported_str = f"{stat.p_comparison or '='} {stat.p_value:.{p_prec}f}"
        computed_str = f"{result.computed_p:.{p_prec}f}"
        wording = (
            PVALUE_DECISION_ERROR_WORDING if result.decision_error else PVALUE_MISMATCH_WORDING
        )
        flags.append(
            ForensicsFlagDraft(
                severity=FlagSeverity.high if result.decision_error else FlagSeverity.med,
                evidence_excerpt=stat.raw_text,
                page_anchor=stat.anchor,
                detail={
                    "kind": "p_value_decision_error"
                    if result.decision_error
                    else "p_value_mismatch",
                    "reason": wording.format(
                        reported_p=reported_str, computed_p=computed_str, test_type=stat.test_type
                    ),
                    "reported_p": stat.p_value,
                    "computed_p": result.computed_p,
                    "test_type": stat.test_type,
                    "one_tailed_in_text": one_tailed_in_text,
                },
            )
        )
    return flags
