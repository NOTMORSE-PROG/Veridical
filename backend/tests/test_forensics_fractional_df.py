"""F6.4/V-033 BUG-491: preserve reported fractional degrees of freedom."""

from dataclasses import replace

import pytest
from scipy import stats
from statcheck.process_stats import process_stats

from app.checks.forensics.extract import extract_all_stats
from app.checks.forensics.pcheck import evaluate_p_recalc, recompute_p
from app.checks.forensics.service import _compute_forensics_findings
from app.models.enums import FlagSeverity, ResultOutcome
from tests.test_checks_forensics_pcheck import _inferential
from tests.test_forensics_notation import NARRATIVE, document


@pytest.mark.parametrize(
    "df,value,p",
    [(2.49, 4.00, 0.039), (4.49, 2.70, 0.048), (2.49, 3.00, 0.073), (1.49, 6.00, 0.051)],
)
@pytest.mark.parametrize("sign", [1, -1])
def test_correct_fractional_t_is_not_a_mismatch(df, value, p, sign):
    stat = _inferential("t", value * sign, p, df2=df)
    result = recompute_p(stat, one_tailed_in_text=False)
    assert result.computed_p == pytest.approx(2 * stats.t.sf(value, df), rel=1e-12)
    assert result.error is result.decision_error is False
    assert evaluate_p_recalc([stat], "No tail description.") == []


def test_public_source_uses_original_fractional_df_probability():
    stat = _inferential("t", 2.58, 0.011, df2=190.82)
    result = recompute_p(stat, one_tailed_in_text=False)
    assert result.computed_p == pytest.approx(0.010631710666211729, rel=1e-12)
    assert result.error is False


@pytest.mark.parametrize("p,severity", [(0.500, FlagSeverity.high), (0.010, FlagSeverity.med)])
def test_genuinely_wrong_fractional_p_still_flags(p, severity):
    stat = _inferential("t", 4.00, p, df2=2.49)
    flags = evaluate_p_recalc([stat], "No tail description.")
    assert len(flags) == 1 and flags[0].severity == severity
    assert flags[0].detail["computed_p"] == pytest.approx(2 * stats.t.sf(4, 2.49))
    assert flags[0].evidence_excerpt == stat.raw_text and flags[0].detail["df2"] == 2.49


@pytest.mark.parametrize(
    "kind,df1,df2,value,reference",
    [
        ("F", 2.49, 4.49, 4.0, lambda: stats.f.sf(4.0, 2.49, 4.49)),
        ("Chi2", 2.49, None, 4.0, lambda: stats.chi2.sf(4.0, 2.49)),
        ("Q", 2.49, None, 4.0, lambda: stats.chi2.sf(4.0, 2.49)),
        ("r", None, 4.49, 0.7, lambda: 2 * stats.t.sf(0.7 * (4.49 / (1 - 0.7**2)) ** 0.5, 4.49)),
    ],
)
def test_other_supported_fractional_distributions_preserve_df(kind, df1, df2, value, reference):
    expected = float(reference())
    stat = _inferential(kind, value, round(expected, 3), df1=df1, df2=df2)
    result = recompute_p(stat, one_tailed_in_text=False)
    assert result.computed_p == pytest.approx(expected, rel=1e-12)
    assert result.error is result.decision_error is False


@pytest.mark.parametrize(
    "kind,value,df1,df2",
    [
        ("t", 2.45, None, 28.0),
        ("F", 3.10, 2.0, 45.0),
        ("Z", 2.45, None, None),
        ("r", 0.4, None, 28.0),
        ("Chi2", 5.67, 2.0, None),
        ("Q", 5.67, 2.0, None),
    ],
)
@pytest.mark.parametrize(
    "options",
    [
        {"one_tailed_in_text": False},
        {"one_tailed_in_text": True},
        {"one_tailed_in_text": False, "two_tailed": False},
        {
            "one_tailed_in_text": True,
            "alpha": 0.1,
            "p_zero_error": False,
            "p_equal_alpha_sig": False,
        },
    ],
)
def test_integer_df_options_match_unmodified_library(kind, value, df1, df2, options):
    stat = _inferential(kind, value, 0.01, df1=df1, df2=df2)
    expected = process_stats(
        kind,
        test_stat=value,
        df1=df1,
        df2=df2,
        reported_p=stat.p_value,
        p_comparison="=",
        test_comparison="=",
        p_dec=3,
        test_dec=2,
        OneTailedInTxt=options["one_tailed_in_text"],
        two_tailed=options.get("two_tailed", True),
        alpha=options.get("alpha", 0.05),
        pZeroError=options.get("p_zero_error", True),
        pEqualAlphaSig=options.get("p_equal_alpha_sig", True),
        OneTailedTxt=True,
        OneTailedTests=not options.get("two_tailed", True),
    ).iloc[0]
    result = recompute_p(stat, **options)
    assert result.computed_p == float(expected["computed_p"])
    assert result.error is bool(expected["error"])
    assert result.decision_error is bool(expected["decision_error"])


def test_fractional_one_tail_mercy_and_explicit_one_tail():
    stat = _inferential("t", 4.0, 0.020, df2=2.49)
    assert evaluate_p_recalc([stat], "A one-tailed test was planned.") == []
    result = recompute_p(stat, one_tailed_in_text=False, two_tailed=False)
    assert result.error is False
    assert result.computed_p == pytest.approx(stats.t.sf(4, 2.49), rel=1e-12)
    assert len(evaluate_p_recalc([stat], "No tail description.")) == 1
    assert evaluate_p_recalc([stat], "Excluded source.", tail_context_complete=False) == []


@pytest.mark.parametrize("comparison,p", [("<", 0.05), (">", 0.03)])
def test_fractional_inequality_uses_original_df(comparison, p):
    stat = _inferential("t", 4.0, p, p_comparison=comparison, df2=2.49)
    assert evaluate_p_recalc([stat], "No tail description.") == []


def test_fractional_valid_result_and_independent_wrong_neighbor_remain_distinct():
    text = "t(2.49)=4.00,p=.039; t(2.49)=4.00,p=.500. " + NARRATIVE
    result = _compute_forensics_findings(document(text))
    assert result.n_inferential == 2 and result.outcome == ResultOutcome.escalated
    assert len(result.flag_drafts) == 1
    assert result.flag_drafts[0].severity == FlagSeverity.high
    assert result.flag_drafts[0].evidence_excerpt == "t(2.49)=4.00,p=.500"
    assert result.image_coverage["statistical_notation_unassessed"] is True
    assert len(result.image_coverage["statistical_notation_coverage"]["items"]) == 1


def test_unavailable_fractional_math_remains_neutral():
    stat = replace(_inferential("t", 4.0, 0.039, df2=2.49), df2=None)
    unassessed = []
    assert evaluate_p_recalc([stat], "No tail description.", unassessed_stats=unassessed) == []
    assert unassessed == [stat]
    assert len(extract_all_stats(document("t(2.49)=4.00,p=.039"))) == 1


@pytest.mark.parametrize("helper", ["compute_p", "error_test", "decision_error_test"])
def test_unavailable_library_probability_or_judgment_is_unassessed(monkeypatch, helper):
    from app.checks.forensics import pcheck

    monkeypatch.setattr(pcheck, helper, lambda **kwargs: None)
    stat = _inferential("t", 4.0, 0.500, df2=2.49)
    unassessed = []
    assert evaluate_p_recalc([stat], "No tail description.", unassessed_stats=unassessed) == []
    assert unassessed == [stat]


def test_unavailable_mercy_cannot_settle_a_mismatch(monkeypatch):
    from app.checks.forensics import pcheck

    original = pcheck.error_test

    def unavailable_one_tail(**kwargs):
        return original(**kwargs) if kwargs["two_tailed"] else None

    monkeypatch.setattr(pcheck, "error_test", unavailable_one_tail)
    stat = _inferential("t", 4.0, 0.020, df2=2.49)
    unassessed = []
    assert (
        evaluate_p_recalc([stat], "A one-tailed test was planned.", unassessed_stats=unassessed)
        == []
    )
    assert unassessed == [stat]
