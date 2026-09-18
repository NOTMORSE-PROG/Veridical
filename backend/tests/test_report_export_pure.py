"""Pure unit tests on `app.report.export`'s helper functions -- no DB, no
live server (same convention as `test_report_export_perf.py`).
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from app.report.export import _format_date, _source_caption, build_report_pdf
from app.report.schemas import CriterionResultOut, ReportExportData, ReportOut, ResolutionOut

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


def test_composite_score_is_not_the_pdf_headline():
    """BUG-133 (ground rule 8): the composite score used to render at
    20pt bold -- twice the size of the 10pt readiness-band badge beside
    it, unlabeled, so it visually dominated the verdict it's supposed to
    be subordinate to. Real PDF bytes, real text extraction (`fitz`, the
    same tool this project's own live export tests already use) -- not a
    guess about what reportlab would render. Asserts the number is still
    present and reproducible (ground rule 8: "the number still exists...
    still appears in the exported detail") but explicitly labeled as
    detail, not the verdict."""
    import fitz

    report = ReportOut(
        check_run_id=1,
        manuscript_group_label="Test Group",
        manuscript_original_filename="thesis.pdf",
        rubric_title="TIP Format v2",
        status="conditionally_ready",
        composite_score=75.0,
        thresholds={"ready_min_score": 85.0, "not_ready_max_score": 60.0},
        reason=None,
        flag_deduction=0.0,
        unresolved_high_flag_count=0,
        llm_mode="real",
        results=[],
        decision=None,
        decided_at=None,
        decision_note=None,
        pending_review_count=0,
        rubric_is_current=True,
        previous_status=None,
        previous_composite_score=None,
    )
    pdf_bytes = build_report_pdf(ReportExportData(report=report, flags=[], archive_size_n=0))

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = "\n".join(page.get_text() for page in doc)
    # The number is still there, reproducible -- ground rule 8 does not
    # ask for it to be hidden, only for it not to be the headline.
    assert "75.0%" in full_text
    # Explicitly labeled as detail, not presented as the verdict itself.
    assert "Composite score:" in full_text
    assert "not the verdict" in full_text

    # Font-size check, not just text presence: the composite-score CAPTION
    # line specifically (BUG-133's actual target, the old 20pt/bold
    # `styles["score"]` span) must genuinely be small now -- a real
    # getComputedStyle-equivalent for a PDF, not an assumption from the
    # source. Anchored on "Composite score:" (unique to this paragraph),
    # not on "75.0%" alone -- the explainer sentence a few lines below
    # ("Ready. The score is 75.0%...") also contains that substring and is
    # a separate, not-yet-ticketed concern (see BUG-133's own ticket: it
    # names only the old oversized span, not that sentence).
    page = doc[0]
    caption_lines = [
        line
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        if any("Composite score:" in span["text"] for span in line["spans"])
    ]
    assert caption_lines, "the 'Composite score:' caption must appear on the first page"
    spans = [span for line in caption_lines for span in line["spans"]]
    sizes = [s["size"] for s in spans]
    assert all(size <= 9 for size in sizes), (
        f"composite score caption must render at caption size (<=9pt), got {sizes}"
    )
