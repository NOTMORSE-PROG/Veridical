"""V-033 tests: statcheck p-value recomputation — the seeded wrong-p demo
scenario (ticket AC #2), the decision-error vs mismatch severity split,
the one-tailed mercy (ticket edge case), and "never guess" applicability
(unsupported test types / missing fields skipped, not flagged)."""

from app.checks.forensics.extract import ReportedStat
from app.checks.forensics.pcheck import (
    PVALUE_DECISION_ERROR_WORDING,
    PVALUE_MISMATCH_WORDING,
    evaluate_p_recalc,
    recompute_p,
)
from app.models.enums import FlagSeverity


def _inferential(
    test_type,
    test_value,
    p_value,
    p_comparison="=",
    *,
    df2=28.0,
    df1=None,
    test_prec=2,
    p_prec=3,
    anchor="p. 12",
):
    return ReportedStat(
        kind="inferential",
        anchor=anchor,
        page=12,
        paragraph=None,
        source="text",
        raw_text=(
            f"{test_type}({df2 if df1 is None else df1}) = {test_value}, p {p_comparison} {p_value}"
        ),
        test_type=test_type,
        df1=df1,
        df2=df2,
        test_value=test_value,
        test_comparison="=",
        p_value=p_value,
        p_comparison=p_comparison,
        test_value_precision=test_prec,
        p_value_precision=p_prec,
    )


def test_correct_p_value_no_flag():
    stat = _inferential("t", 2.45, 0.021)
    flags = evaluate_p_recalc([stat], "no mention of tails")
    assert flags == []


def test_seeded_wrong_p_flagged_with_both_values_shown():
    """Ticket AC #2: seeded wrong-p -> flagged w/ both values shown."""
    stat = _inferential("t", 2.45, 0.500)
    flags = evaluate_p_recalc([stat], "no mention")
    assert len(flags) == 1
    flag = flags[0]
    assert flag.detail["reported_p"] == 0.500
    assert round(flag.detail["computed_p"], 3) == 0.021
    assert "0.500" in flag.detail["reason"]
    assert "0.021" in flag.detail["reason"]


def test_decision_error_is_high_severity():
    """A p that flips the significance conclusion (.500 vs a truly
    significant .021) is more severe than a same-conclusion rounding
    mismatch — matches the retracted-source precedent (V-029) of reserving
    HIGH severity for findings that change what the instructor should
    conclude, not just an arithmetic nitpick."""
    stat = _inferential("t", 2.45, 0.500)
    flags = evaluate_p_recalc([stat], "no mention")
    assert flags[0].severity == FlagSeverity.high
    assert flags[0].detail["kind"] == "p_value_decision_error"
    expected = PVALUE_DECISION_ERROR_WORDING.format(
        reported_p="= 0.500", computed_p="0.021", test_type="t"
    )
    assert flags[0].detail["reason"] == expected


def test_mismatch_same_conclusion_is_medium_severity():
    """Both .010 (reported) and ~.021 (computed) are < .05 — a real
    mismatch, but the significance conclusion doesn't change."""
    stat = _inferential("t", 2.45, 0.010)
    flags = evaluate_p_recalc([stat], "no mention")
    assert flags[0].severity == FlagSeverity.med
    assert flags[0].detail["kind"] == "p_value_mismatch"
    expected = PVALUE_MISMATCH_WORDING.format(
        reported_p="= 0.010", computed_p="0.021", test_type="t"
    )
    assert flags[0].detail["reason"] == expected


def test_one_tailed_mercy_suppresses_a_false_flag():
    """Ticket edge case: 'don't flag if one-tailed would make it
    consistent — inherit that mercy'. t(28)=2.45 at p=.010 only matches a
    ONE-tailed computation; a document that says so must not be flagged."""
    stat = _inferential("t", 2.45, 0.010)
    full_text = "This study used a one-tailed test throughout the analysis."
    flags = evaluate_p_recalc([stat], full_text)
    assert flags == []


def test_unsupported_test_type_returns_none_not_a_guess():
    stat = _inferential("Qb", 3.0, 0.05)
    assert recompute_p(stat, one_tailed_in_text=False) is None


def test_missing_precision_returns_none():
    stat = _inferential("t", 2.45, 0.021)
    stat_no_prec = ReportedStat(
        kind="inferential",
        anchor=stat.anchor,
        page=stat.page,
        paragraph=None,
        source="text",
        raw_text=stat.raw_text,
        test_type="t",
        df2=28.0,
        test_value=2.45,
        p_value=0.021,
        test_value_precision=None,
        p_value_precision=None,
    )
    assert recompute_p(stat_no_prec, one_tailed_in_text=False) is None


def test_descriptive_stat_never_recomputed():
    descriptive = ReportedStat(
        kind="descriptive",
        anchor="p. 5",
        page=5,
        paragraph=None,
        source="table",
        raw_text="30",
        stat_name="n",
        value=30.0,
    )
    assert recompute_p(descriptive, one_tailed_in_text=False) is None


def test_multiple_stats_each_evaluated_independently():
    stats = [
        _inferential("t", 2.45, 0.021, anchor="p. 1"),  # correct
        _inferential("F", 3.10, 0.900, df1=2.0, df2=45.0, anchor="p. 2"),  # wrong
    ]
    flags = evaluate_p_recalc(stats, "no mention")
    assert len(flags) == 1
    assert flags[0].page_anchor == "p. 2"


def test_wording_never_uses_accusatory_language():
    for wording in (PVALUE_MISMATCH_WORDING, PVALUE_DECISION_ERROR_WORDING):
        lowered = wording.lower()
        assert "fake" not in lowered
        assert "fabricat" not in lowered
        assert "cheat" not in lowered
        assert "dishonest" not in lowered
