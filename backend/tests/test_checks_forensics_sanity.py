"""V-033 tests: sanity checks — percentages summing to ~100% and group
counts not exceeding a stated total (ticket AC #4: "Percent-sum violation
beyond tolerance → low-severity flag"; boundary tests at 99.9%/100.4%)."""

from app.checks.forensics.extract import ReportedStat
from app.checks.forensics.keywords import load_keywords
from app.checks.forensics.sanity import (
    GROUP_COUNT_EXCEEDS_TOTAL_WORDING,
    PERCENT_SUM_TOLERANCE,
    PERCENT_SUM_WORDING,
    evaluate_group_counts,
    evaluate_percentage_sums,
)
from app.models.enums import FlagSeverity


def _pct(value, raw, *, anchor="p. 10", group_label="A"):
    return ReportedStat(
        kind="descriptive",
        anchor=anchor,
        page=10,
        paragraph=None,
        source="table",
        raw_text=raw,
        stat_name="percentage",
        value=value,
        group_label=group_label,
    )


def _n(value, raw, *, anchor="p. 10", group_label="A"):
    return ReportedStat(
        kind="descriptive",
        anchor=anchor,
        page=10,
        paragraph=None,
        source="table",
        raw_text=raw,
        stat_name="n",
        value=value,
        group_label=group_label,
    )


# --- percentage sums ---------------------------------------------------------


def test_percentages_summing_to_exactly_100_no_flag():
    stats = [_pct(40.0, "40", group_label="A"), _pct(60.0, "60", group_label="B")]
    assert evaluate_percentage_sums(stats) == []


def test_percentages_within_tolerance_no_flag():
    """Boundary: 99.9% and 100.4% (ticket's own named boundary values)
    both sit inside the default 1.0pt tolerance."""
    assert evaluate_percentage_sums([_pct(49.9, "49.9"), _pct(50.0, "50.0")]) == []  # 99.9%
    assert evaluate_percentage_sums([_pct(50.2, "50.2"), _pct(50.2, "50.2")]) == []  # 100.4%


def test_percentages_beyond_tolerance_flagged_low_severity():
    stats = [_pct(40.0, "40", group_label="A"), _pct(50.0, "50", group_label="B")]
    flags = evaluate_percentage_sums(stats)
    assert len(flags) == 1
    assert flags[0].severity == FlagSeverity.low
    assert flags[0].detail["kind"] == "percentage_sum_off"
    assert flags[0].detail["total"] == 90.0
    expected = PERCENT_SUM_WORDING.format(total=90.0, tolerance=PERCENT_SUM_TOLERANCE)
    assert flags[0].detail["reason"] == expected


def test_single_percentage_alone_not_flagged():
    """One percentage says nothing about 'sums to 100' — never a false
    positive from an incomplete table read."""
    assert evaluate_percentage_sums([_pct(45.0, "45")]) == []


def test_low_confidence_percentages_excluded():
    stats = [
        ReportedStat(
            kind="descriptive",
            anchor="p. 1",
            page=1,
            paragraph=None,
            source="image_table",
            raw_text="40",
            stat_name="percentage",
            value=40.0,
            low_confidence=True,
            group_label="A",
        ),
        ReportedStat(
            kind="descriptive",
            anchor="p. 1",
            page=1,
            paragraph=None,
            source="image_table",
            raw_text="50",
            stat_name="percentage",
            value=50.0,
            low_confidence=True,
            group_label="B",
        ),
    ]
    assert evaluate_percentage_sums(stats) == []


def test_different_tables_evaluated_independently():
    stats = [
        _pct(40.0, "40", anchor="p. 1", group_label="A"),
        _pct(60.0, "60", anchor="p. 1", group_label="B"),  # sums to 100, fine
        _pct(30.0, "30", anchor="p. 2", group_label="C"),
        _pct(30.0, "30", anchor="p. 2", group_label="D"),  # sums to 60, off
    ]
    flags = evaluate_percentage_sums(stats)
    assert len(flags) == 1
    assert flags[0].page_anchor == "p. 2"


# --- group counts vs stated total -------------------------------------------


def test_group_counts_matching_total_no_flag():
    keywords = load_keywords()
    stats = [
        _n(30.0, "30", group_label="Control"),
        _n(30.0, "30", group_label="Treatment"),
        _n(60.0, "60", group_label="Total"),
    ]
    assert evaluate_group_counts(stats, keywords) == []


def test_group_counts_exceeding_total_flagged_low_severity():
    keywords = load_keywords()
    stats = [
        _n(40.0, "40", group_label="Control"),
        _n(40.0, "40", group_label="Treatment"),
        _n(60.0, "60", group_label="Total"),
    ]
    flags = evaluate_group_counts(stats, keywords)
    assert len(flags) == 1
    assert flags[0].severity == FlagSeverity.low
    assert flags[0].detail["kind"] == "group_count_exceeds_total"
    expected = GROUP_COUNT_EXCEEDS_TOTAL_WORDING.format(group_sum=80, total=60)
    assert flags[0].detail["reason"] == expected


def test_no_total_row_no_flag():
    """No stated total to compare against — never guessed."""
    stats = [_n(30.0, "30", group_label="Control"), _n(30.0, "30", group_label="Treatment")]
    assert evaluate_group_counts(stats, load_keywords()) == []


def test_single_group_with_total_not_flagged():
    """Only one real group plus a total isn't a 'do groups sum to total'
    scenario worth checking."""
    stats = [_n(30.0, "30", group_label="Control"), _n(30.0, "30", group_label="Total")]
    assert evaluate_group_counts(stats, load_keywords()) == []


def test_wording_never_uses_accusatory_language():
    for wording in (
        PERCENT_SUM_WORDING.format(total=90, tolerance=1.0),
        GROUP_COUNT_EXCEEDS_TOTAL_WORDING.format(group_sum=1, total=1),
    ):
        lowered = wording.lower()
        assert "fake" not in lowered
        assert "fabricat" not in lowered
        assert "cheat" not in lowered
