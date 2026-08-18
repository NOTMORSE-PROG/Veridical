"""Pure unit tests on `app.report.export`'s helper functions -- no DB, no
live server (same convention as `test_report_export_perf.py`).
"""

import json
from pathlib import Path

from app.report.export import _source_caption
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
