"""F5: saved attempt observations are quantities, never guessed from flags."""

import pytest

from app.report.schemas import COVERAGE_REASONS
from app.report.service import _reference_metadata_attempts


def saved(*attempts):
    return {
        "n_references": len(attempts),
        "reference_metadata_coverage": [
            {"reference_order_index": i, "lookup_attempted": value}
            for i, value in enumerate(attempts)
        ],
    }


def test_complete_saved_inventory_derives_actual_attempts():
    assert _reference_metadata_attempts(saved(True, False, True)) == 2
    assert _reference_metadata_attempts(saved(False, False)) == 0
    assert _reference_metadata_attempts(saved()) == 0
    assert "reference_membership_partial" in COVERAGE_REASONS


@pytest.mark.parametrize(
    "change", ["missing", "incomplete", "duplicate", "nonboolean", "booltotal"]
)
def test_ambiguous_history_never_becomes_zero(change):
    d = saved(True, False)
    if change == "missing":
        d.pop("reference_metadata_coverage")
    if change == "incomplete":
        d["reference_metadata_coverage"].pop()
    if change == "duplicate":
        d["reference_metadata_coverage"][1]["reference_order_index"] = 0
    if change == "nonboolean":
        d["reference_metadata_coverage"][1]["lookup_attempted"] = 0
    if change == "booltotal":
        d["n_references"] = True
    assert _reference_metadata_attempts(d) is None


def test_explicit_recorded_count_remains_authoritative():
    assert _reference_metadata_attempts({"n_reference_metadata_attempted": 4}) == 4
    assert _reference_metadata_attempts({"n_reference_metadata_attempted": 0}) == 0
    assert _reference_metadata_attempts({"n_reference_metadata_attempted": True}) is None
