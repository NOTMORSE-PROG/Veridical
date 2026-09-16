"""Pure unit tests on `app.report.export`'s helper functions -- no DB, no
live server (same convention as `test_report_export_perf.py`).
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from app.report.export import _format_date, _source_caption
from app.report.schemas import CriterionResultOut, ResolutionOut

_FIXTURE = (
    Path(__file__).resolve().parent.parent.parent
    / "tests"
    / "fixtures"
    / "source_caption_cases.json"
)

_RESOLUTION = ResolutionOut(type="mark_pass", reason="Verified manually.", ai_majority_verdict=None)


def _row(case: dict) -> CriterionResultOut:
    return CriterionResultOut(
        criterion_id=1,
        text="Has a references section",
        type=case["type"],
        weight=10.0,
        weight_importance="med",
        kind=case["kind"],
        outcome="passed",
        score=100.0,
        basis=None,
        anchor=None,
        reasoning=None,
        reason=None,
        evidence=[],
        resolution=_RESOLUTION if case["has_resolution"] else None,
    )


def test_format_date_labels_a_time_explicitly_as_utc():
    """BUG-059: the exported PDF used to stamp `datetime.now()` (naive,
    the server's own local wall clock) with no zone marker at all -- read
    as if it were the instructor's own local time (Asia/Manila), an
    8-hour discrepancy on the record whose purpose is to be one. The
    caller now passes a real UTC instant and the label makes it
    unambiguous, rather than silently converting to an assumed viewer
    timezone (this PDF can be opened by an adviser anywhere, not just the
    instructor who generated it)."""
    fixed = datetime(2026, 8, 16, 3, 42, tzinfo=UTC)
    assert _format_date(fixed, with_time=True) == "Aug 16, 2026, 3:42 AM UTC"


def test_format_date_without_time_has_no_zone_label():
    # A date-only rendering (report.decided_at) carries no time-of-day,
    # so a zone label would be noise, not honesty -- unchanged by BUG-059.
    fixed = datetime(2026, 8, 16, 3, 42, tzinfo=UTC)
    assert _format_date(fixed, with_time=False) == "Aug 16, 2026"


def test_source_caption_matches_the_shared_contract_fixture():
    """BUG-082: `_source_caption` (this file) and `sourceCaption`
    (`ResultsTable.tsx`) implement the identical "Rule-checked vs
    AI-graded" rule in two languages -- they diverged silently (one read
    `type`, the other `kind`) with no test to catch it. This asserts THIS
    implementation against the shared contract fixture; the frontend has
    its own sibling test asserting the same fixture
    (`frontend/src/report/ResultsTable.contract.test.ts`). See `tests/
    fixtures/README.md`."""
    cases = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert cases, "fixture must not be empty -- an empty fixture passes vacuously"
    for case in cases:
        assert _source_caption(_row(case)) == case["expected_caption"], case["name"]
