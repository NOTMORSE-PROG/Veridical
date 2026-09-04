"""V-032 tests: applicability-gated GRIM/GRIMMER evaluation over V-031's
extracted stats — the seeded-error demo case (ticket AC #2: "Seeded
GRIM-impossible mean → flagged with the arithmetic displayed") and the
applicability guard (ticket AC #3: "Non-integer/unknown-scale data →
skipped + logged, not guessed")."""

from app.checks.forensics.checks import (
    GRIM_INCONSISTENT_WORDING,
    GRIMMER_INCONSISTENT_WORDING,
    evaluate_grim_grimmer,
)
from app.checks.forensics.extract import ReportedStat
from app.models.enums import FlagSeverity


def _descriptive(
    stat_name,
    value,
    raw_text,
    *,
    anchor="p. 20",
    group_label="Control",
    low_confidence=False,
    table_index=0,
):
    return ReportedStat(
        kind="descriptive",
        anchor=anchor,
        page=20,
        paragraph=None,
        source="table",
        raw_text=raw_text,
        low_confidence=low_confidence,
        stat_name=stat_name,
        value=value,
        group_label=group_label,
        table_index=table_index,
    )


def test_seeded_grim_impossible_mean_is_flagged_with_arithmetic():
    """Ticket AC #2's own demo scenario: n=10, mean=3.33 — no integer sum
    of 10 responses averages to 3.33 at 2 decimal places."""
    stats = [
        _descriptive("n", 10.0, "10"),
        _descriptive("mean", 3.33, "3.33"),
    ]
    flags = evaluate_grim_grimmer(stats).flags
    assert len(flags) == 1
    flag = flags[0]
    assert flag.severity == FlagSeverity.med
    assert flag.detail["kind"] == "grim_inconsistent"
    assert flag.detail["n"] == 10
    assert flag.detail["mean"] == 3.33
    assert flag.page_anchor == "p. 20"
    assert "10" in flag.evidence_excerpt and "3.33" in flag.evidence_excerpt
    expected = GRIM_INCONSISTENT_WORDING.format(mean="3.33", n=10)
    assert flag.detail["reason"] == expected


def test_consistent_mean_produces_no_flag():
    """A faithful, achievable mean must NOT be flagged — matches the
    established "confirmed = silence" pattern from every other check
    family this session built (V-027/V-029/V-030)."""
    stats = [
        _descriptive("n", 10.0, "10"),
        _descriptive("mean", 3.30, "3.30"),
    ]
    assert evaluate_grim_grimmer(stats).flags == []


def test_grimmer_inconsistent_sd_flagged_when_mean_is_consistent():
    stats = [
        _descriptive("n", 18.0, "18"),
        _descriptive("mean", 3.44, "3.44"),
        _descriptive("sd", 2.47, "2.47"),
    ]
    flags = evaluate_grim_grimmer(stats).flags
    assert len(flags) == 1
    assert flags[0].detail["kind"] == "grimmer_inconsistent"
    assert flags[0].severity == FlagSeverity.med
    expected = GRIMMER_INCONSISTENT_WORDING.format(sd="2.47", mean="3.44", n=18)
    assert flags[0].detail["reason"] == expected


def test_grim_failure_does_not_also_run_grimmer():
    """When the mean itself already fails GRIM, GRIMMER can never pass —
    only one flag (the GRIM one) should be produced, not a redundant
    second finding for the same underlying inconsistency."""
    stats = [
        _descriptive("n", 10.0, "10"),
        _descriptive("mean", 3.33, "3.33"),
        _descriptive("sd", 1.2, "1.2"),
    ]
    flags = evaluate_grim_grimmer(stats).flags
    assert len(flags) == 1
    assert flags[0].detail["kind"] == "grim_inconsistent"


def test_missing_n_or_mean_skipped_not_guessed():
    """Ticket AC #3: unknown/incomplete data is skipped, never guessed."""
    only_mean = [_descriptive("mean", 3.33, "3.33")]
    assert evaluate_grim_grimmer(only_mean).flags == []

    only_n = [_descriptive("n", 10.0, "10")]
    assert evaluate_grim_grimmer(only_n).flags == []


def test_missing_sd_skips_grimmer_but_grim_still_runs():
    stats = [_descriptive("n", 10.0, "10"), _descriptive("mean", 3.33, "3.33")]
    flags = evaluate_grim_grimmer(stats).flags
    assert len(flags) == 1
    assert flags[0].detail["kind"] == "grim_inconsistent"


def test_low_confidence_vision_table_row_skipped():
    """V-007 contract, same as V-031's own extraction gate — never feed a
    forensics check with a vision read the model wasn't sure about."""
    stats = [
        _descriptive("n", 10.0, "10", low_confidence=True),
        _descriptive("mean", 3.33, "3.33", low_confidence=True),
    ]
    assert evaluate_grim_grimmer(stats).flags == []


def test_different_groups_evaluated_independently():
    stats = [
        _descriptive("n", 10.0, "10", group_label="Control"),
        _descriptive("mean", 3.33, "3.33", group_label="Control"),  # inconsistent
        _descriptive("n", 20.0, "20", group_label="Treatment"),
        _descriptive("mean", 3.50, "3.50", group_label="Treatment"),  # consistent (70/20)
    ]
    flags = evaluate_grim_grimmer(stats).flags
    assert len(flags) == 1
    assert "Control" in flags[0].evidence_excerpt
    assert "Treatment" not in flags[0].evidence_excerpt


def test_different_tables_never_cross_paired():
    """Two tables reporting an 'n' and a 'mean' separately (different
    anchors) must never be paired into a false candidate — a real
    correctness requirement of the (anchor, group_label) grouping key."""
    stats = [
        _descriptive("n", 10.0, "10", anchor="p. 5"),
        _descriptive("mean", 3.33, "3.33", anchor="p. 9"),
    ]
    assert evaluate_grim_grimmer(stats).flags == []


def test_repeated_n_within_one_table_skips_the_whole_table_not_guessed():
    """BUG-164: the single most common capstone table shape reports a
    WEIGHTED mean of k sub-items over the SAME n respondents, once per
    criterion/indicator row — GRIM's own math assumes n respondents each
    contributing ONE integer item response, violated here, and measured
    at a 67-81% false-positive rate on genuinely correct means (ticket's
    own 2,000-trial simulation). No scale/item-count metadata exists to
    know k directly, but the repeated n across three "criteria" rows of
    the SAME table (anchor) is a real, purely structural signal that
    this is a multi-item summary, not three independent single-item
    measurements — n=10 with mean=3.33 alone would be GRIM-inconsistent
    (the seeded-demo scenario above), but paired with two sibling rows
    sharing the identical n=10, every row in this table must be SKIPPED,
    never flagged, and the skip must be disclosed, not silently
    dropped."""
    stats = [
        _descriptive("n", 10.0, "10", group_label="Functionality"),
        _descriptive("mean", 3.33, "3.33", group_label="Functionality"),  # GRIM-impossible alone
        _descriptive("n", 10.0, "10", group_label="Usability"),
        _descriptive("mean", 3.50, "3.50", group_label="Usability"),
        _descriptive("n", 10.0, "10", group_label="Overall"),
        _descriptive("mean", 3.40, "3.40", group_label="Overall"),
    ]
    result = evaluate_grim_grimmer(stats)
    assert result.flags == []
    assert result.skipped_composite_rows == 3


def test_two_distinct_single_row_tables_on_the_same_page_are_never_merged():
    """`backend-critic` finding (BUG-164 review, live-reproduced): `anchor`
    is a PAGE string ("p. 20"), not a table identity — PyMuPDF's own
    per-page `find_tables()` (BUG-163) commonly returns SEVERAL distinct
    tables on one page (e.g. two small single-row summary tables stacked
    together, a real capstone layout). Two genuinely unrelated tables
    that happen to share both a page and an n value must NOT be merged
    into one "composite" group and both silently skipped — that would
    suppress a real GRIM inconsistency in either one. `table_index`
    (the real position within `ExtractionResult.tables`) is what
    disambiguates them; without it, this exact scenario returned zero
    flags and `skipped_composite_rows == 2` instead of the one real
    finding below."""
    stats = [
        # Table 0: a genuinely GRIM-impossible single-item mean.
        _descriptive("n", 10.0, "10", group_label="Usability", table_index=0),
        _descriptive("mean", 3.33, "3.33", group_label="Usability", table_index=0),
        # Table 1: an unrelated table, same page, same n by coincidence,
        # genuinely single-item and GRIM-consistent.
        _descriptive("n", 10.0, "10", group_label="Reliability", table_index=1),
        _descriptive("mean", 3.30, "3.30", group_label="Reliability", table_index=1),
    ]
    result = evaluate_grim_grimmer(stats)
    assert result.skipped_composite_rows == 0
    assert len(result.flags) == 1
    assert result.flags[0].detail["kind"] == "grim_inconsistent"
    assert "Usability" in result.flags[0].evidence_excerpt
    assert "Reliability" not in result.flags[0].evidence_excerpt


def test_a_single_row_table_with_no_repeated_n_still_runs_grim_normally():
    """The composite-table guard must not become a blanket "never run
    GRIM" regression — a table with exactly one criterion/row (no
    opportunity for n to repeat) is exactly F6.2's own seeded-demo shape
    and must still be evaluated, unaffected by BUG-164's fix."""
    stats = [
        _descriptive("n", 10.0, "10"),
        _descriptive("mean", 3.33, "3.33"),
    ]
    result = evaluate_grim_grimmer(stats)
    assert len(result.flags) == 1
    assert result.flags[0].detail["kind"] == "grim_inconsistent"
    assert result.skipped_composite_rows == 0


def test_wording_never_uses_accusatory_language():
    for wording in (GRIM_INCONSISTENT_WORDING, GRIMMER_INCONSISTENT_WORDING):
        lowered = wording.lower()
        assert "fake" not in lowered
        assert "fabricat" not in lowered
        assert "cheat" not in lowered
        assert "dishonest" not in lowered
