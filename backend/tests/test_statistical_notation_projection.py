"""F6/F8: recorded coverage is tri-state and private/PDF copy stays aligned."""

import json
from pathlib import Path

import pytest

from app.report.service import _statistical_notation_state
from app.report.statistical_scope import StatisticalCheckScopeOut, statistical_scope_copy


@pytest.mark.parametrize(
    "detail,expected",
    [
        (None, None),
        ({}, None),
        ({"statistical_notation_unassessed": False}, None),
        (
            {
                "statistical_notation_unassessed": True,
                "statistical_notation_coverage": {"version": "v2", "items": [{}]},
            },
            None,
        ),
        (
            {
                "statistical_notation_unassessed": False,
                "statistical_notation_coverage": {"version": "v1", "items": []},
            },
            False,
        ),
        (
            {
                "statistical_notation_unassessed": True,
                "statistical_notation_coverage": {"version": "v1", "items": [{}]},
            },
            True,
        ),
        (
            {
                "statistical_notation_unassessed": False,
                "statistical_notation_coverage": {"version": "v1", "items": [{}]},
            },
            None,
        ),
        (
            {
                "statistical_notation_unassessed": 1,
                "statistical_notation_coverage": {"version": "v1", "items": [{}]},
            },
            None,
        ),
    ],
)
def test_missing_history_is_not_a_recorded_empty_coverage(detail, expected):
    assert _statistical_notation_state(detail) is expected


def test_preserved_historical_scope_does_not_assert_absence_of_statistics():
    _, body = statistical_scope_copy(StatisticalCheckScopeOut(state="not_applicable"))
    assert "saved applicability result" in body
    assert "does not establish that the manuscript contains no statistics" in body


def test_private_report_and_pdf_notation_copy_match():
    root = Path(__file__).resolve().parents[2]
    frontend = json.loads((root / "frontend/src/report/statisticalNotationCopy.json").read_text())
    backend = json.loads((root / "backend/app/report/data/coverage_labels.json").read_text())
    for key in ("partial", "unsupported", "limit"):
        assert frontend[key] == backend["statistical_notation_" + key]
