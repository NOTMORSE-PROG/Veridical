"""V-033 tests: sanity checks — percentages summing to ~100% and group
counts not exceeding a stated total (ticket AC #4: "Percent-sum violation
beyond tolerance → low-severity flag"; boundary tests at 99.9%/100.4%)."""

from app.checks.forensics.extract import ReportedStat, extract_descriptive_stats
from app.checks.forensics.keywords import load_keywords
from app.checks.forensics.sanity import (
    GROUP_COUNT_EXCEEDS_TOTAL_WORDING,
    PERCENT_SUM_TOLERANCE,
    PERCENT_SUM_WORDING,
    evaluate_group_counts,
    evaluate_percentage_sums,
)
from app.ingest.schemas import TableBlock
from app.models.enums import FlagSeverity


def _pct(value, raw, *, anchor="p. 10", group_label="A", table_index=None):
    return ReportedStat(
        table_index=table_index,
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
    stats = [_pct(40.0, "40", group_label="A"), _pct(55.0, "55", group_label="B")]
    flags = evaluate_percentage_sums(stats)
    assert len(flags) == 1
    assert flags[0].severity == FlagSeverity.low
    assert flags[0].detail["kind"] == "percentage_sum_off"
    assert flags[0].detail["total"] == 95.0
    expected = PERCENT_SUM_WORDING.format(total=95.0, tolerance=PERCENT_SUM_TOLERANCE)
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
        _pct(40.0, "40", anchor="p. 2", group_label="C"),
        _pct(55.0, "55", anchor="p. 2", group_label="D"),  # sums to 95, off
    ]
    flags = evaluate_percentage_sums(stats)
    assert len(flags) == 1
    assert flags[0].page_anchor == "p. 2"


def test_two_tables_on_one_page_are_not_summed_together():
    stats = [
        _pct(60.0, "60", group_label="A", table_index=0),
        _pct(40.0, "40", group_label="B", table_index=0),
        _pct(70.0, "70", group_label="C", table_index=1),
        _pct(30.0, "30", group_label="D", table_index=1),
    ]
    assert evaluate_percentage_sums(stats) == []


def _table_stats(rows, *, caption=None):
    table = TableBlock(page=31, rows=rows, caption=caption)
    return table, extract_descriptive_stats([table])


def test_independent_rates_over_100_are_not_a_distribution():
    # Penetration per 100 people: a mobile rate above 100% is ordinary.
    table, stats = _table_stats(
        [["Indicator", "%"], ["Telephones", "16%"], ["Mobile", "195 %"], ["Internet Users", "44%"]]
    )
    assert evaluate_percentage_sums(stats, tables=[table]) == []


def test_rates_that_stay_under_100_without_a_cue_are_not_summed():
    table, stats = _table_stats([["Programme", "%"], ["BSIT", "35%"], ["BSCS", "20%"]])
    assert evaluate_percentage_sums(stats, tables=[table]) == []


def test_rate_header_blocks_the_check_even_near_100():
    table, stats = _table_stats([["Pass rate by programme", "%"], ["BSIT", "55%"], ["BSCS", "40%"]])
    assert evaluate_percentage_sums(stats, tables=[table]) == []


def test_distribution_with_frequencies_summing_to_104_is_flagged():
    table, stats = _table_stats(
        [
            ["Response", "f", "%"],
            ["Strongly agree", "10", "29%"],
            ["Agree", "10", "25%"],
            ["Disagree", "10", "25%"],
            ["Strongly disagree", "10", "25%"],
        ]
    )
    flags = evaluate_percentage_sums(stats, tables=[table])
    assert len(flags) == 1 and flags[0].detail["total"] == 104.0


def test_frequencies_that_reproduce_the_shares_make_a_short_sum_checkable():
    table, stats = _table_stats(
        [
            ["Year level", "f", "%"],
            ["First", "10", "10%"],
            ["Second", "20", "20%"],
            ["Third", "30", "30%"],
            ["Fourth", "40", "30%"],
        ]
    )
    # 90% in total, but the counts say the shares should be 10/20/30/40.
    flags = evaluate_percentage_sums(stats, tables=[table])
    assert len(flags) == 1 and flags[0].detail["whole_basis"] == "frequencies_match"


def test_independent_rates_stay_silent_in_the_reviewed_shapes():
    cases = [
        # no cue, sum 150, an Overall row holding an average rate
        [["Programme", "%"], ["BSIT", "60%"], ["BSCS", "50%"], ["Overall", "40%"]],
        # plural cue word
        [["Pass rates by programme", "%"], ["BSIT", "55%"], ["BSCS", "40%"]],
        # per-section totals are not one distribution
        [["Item", "%"], ["A", "60%"], ["Total", "100%"], ["B", "70%"], ["Total", "100%"]],
        # a coincidence on one row of an N table
        [["Group", "N", "%"], ["X", "50", "50%"], ["Y", "50", "95%"]],
    ]
    for rows in cases:
        table, stats = _table_stats(rows)
        assert evaluate_percentage_sums(stats, tables=[table]) == [], rows


def test_a_decorated_total_label_still_counts_as_a_total_row():
    table, stats = _table_stats(
        [["Sex", "%"], ["Male", "30%"], ["Female", "50%"], ["Total (n=120):", "100%"]]
    )
    assert len(evaluate_percentage_sums(stats, tables=[table])) == 1


def test_a_total_row_makes_the_sum_checkable_and_is_not_counted_twice():
    table, stats = _table_stats(
        [["Sex", "%"], ["Male", "30%"], ["Female", "50%"], ["Total", "100%"]]
    )
    flags = evaluate_percentage_sums(stats, tables=[table])
    assert len(flags) == 1 and flags[0].detail["total"] == 80.0
    correct, ok = _table_stats(
        [["Sex", "%"], ["Male", "40%"], ["Female", "60%"], ["Total", "100%"]]
    )
    assert evaluate_percentage_sums(ok, tables=[correct]) == []


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
