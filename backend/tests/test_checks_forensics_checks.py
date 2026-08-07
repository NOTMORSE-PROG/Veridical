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
    stat_name, value, raw_text, *, anchor="p. 20", group_label="Control", low_confidence=False
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
    )


def test_seeded_grim_impossible_mean_is_flagged_with_arithmetic():
    """Ticket AC #2's own demo scenario: n=10, mean=3.33 — no integer sum
    of 10 responses averages to 3.33 at 2 decimal places."""
    stats = [
        _descriptive("n", 10.0, "10"),
        _descriptive("mean", 3.33, "3.33"),
    ]
    flags = evaluate_grim_grimmer(stats)
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
    assert evaluate_grim_grimmer(stats) == []


def test_grimmer_inconsistent_sd_flagged_when_mean_is_consistent():
    stats = [
        _descriptive("n", 18.0, "18"),
        _descriptive("mean", 3.44, "3.44"),
        _descriptive("sd", 2.47, "2.47"),
    ]
    flags = evaluate_grim_grimmer(stats)
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
    flags = evaluate_grim_grimmer(stats)
    assert len(flags) == 1
    assert flags[0].detail["kind"] == "grim_inconsistent"


def test_missing_n_or_mean_skipped_not_guessed():
    """Ticket AC #3: unknown/incomplete data is skipped, never guessed."""
    only_mean = [_descriptive("mean", 3.33, "3.33")]
    assert evaluate_grim_grimmer(only_mean) == []

    only_n = [_descriptive("n", 10.0, "10")]
    assert evaluate_grim_grimmer(only_n) == []


def test_missing_sd_skips_grimmer_but_grim_still_runs():
    stats = [_descriptive("n", 10.0, "10"), _descriptive("mean", 3.33, "3.33")]
    flags = evaluate_grim_grimmer(stats)
    assert len(flags) == 1
    assert flags[0].detail["kind"] == "grim_inconsistent"


def test_low_confidence_vision_table_row_skipped():
    """V-007 contract, same as V-031's own extraction gate — never feed a
    forensics check with a vision read the model wasn't sure about."""
    stats = [
        _descriptive("n", 10.0, "10", low_confidence=True),
        _descriptive("mean", 3.33, "3.33", low_confidence=True),
    ]
    assert evaluate_grim_grimmer(stats) == []


def test_different_groups_evaluated_independently():
    stats = [
        _descriptive("n", 10.0, "10", group_label="Control"),
        _descriptive("mean", 3.33, "3.33", group_label="Control"),  # inconsistent
        _descriptive("n", 20.0, "20", group_label="Treatment"),
        _descriptive("mean", 3.50, "3.50", group_label="Treatment"),  # consistent (70/20)
    ]
    flags = evaluate_grim_grimmer(stats)
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
    assert evaluate_grim_grimmer(stats) == []


def test_wording_never_uses_accusatory_language():
    for wording in (GRIM_INCONSISTENT_WORDING, GRIMMER_INCONSISTENT_WORDING):
        lowered = wording.lower()
        assert "fake" not in lowered
        assert "fabricat" not in lowered
        assert "cheat" not in lowered
        assert "dishonest" not in lowered
