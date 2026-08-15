"""V-039 AC: a 60-criterion report exports in <30s with a <50MB memory
spike (measured, not assumed) -- the exact numbers the ticket names.
Pure unit test on `build_report_pdf`, no live DB needed (this is a
render-speed/memory property of the function itself, not the data
layer)."""

import time
import tracemalloc

from app.report.export import build_report_pdf
from app.report.schemas import (
    CriterionResultOut,
    EvidenceItem,
    FlagSummaryOut,
    ReportExportData,
    ReportOut,
)

_MAX_SECONDS = 30
_MAX_MEMORY_MB = 50


def _sixty_criterion_report() -> ReportExportData:
    results = [
        CriterionResultOut(
            criterion_id=i,
            text=f"Criterion {i}: checks a specific structural or content requirement of chapter "
            "formatting, in enough detail to be realistic.",
            type="semantic" if i % 2 else "structural",
            weight=100 / 60,
            kind="semantic_grading",
            outcome="passed" if i % 3 else "failed",
            score=90.0,
            basis=None,
            anchor="page 3",
            reasoning=(
                "The manuscript demonstrates clear evidence of meeting this criterion based on "
                "the extracted content and structure. " * 2
            ),
            reason=None,
            evidence=[
                EvidenceItem(
                    quote=(
                        "A representative excerpt from the manuscript showing compliance with "
                        "this criterion, spanning a couple of sentences for realism."
                    ),
                    anchor="page 3, paragraph 2",
                )
            ],
            resolution=None,
        )
        for i in range(60)
    ]
    flags = [
        FlagSummaryOut(
            id=i,
            check_kind="citation_integrity",
            severity="high" if i % 4 == 0 else "low",
            criterion_text=None,
            evidence_excerpt="Citation excerpt text repeated for realistic length. " * 5,
            page_anchor=f"p. {i}",
            overridden=False,
        )
        for i in range(15)
    ]
    report = ReportOut(
        check_run_id=1,
        manuscript_group_label="Test Group",
        manuscript_original_filename="thesis.pdf",
        rubric_title="TIP Format v2",
        status="conditionally_ready",
        composite_score=72.5,
        thresholds={"ready_min_score": 85.0, "not_ready_max_score": 60.0},
        reason=None,
        flag_deduction=2.5,
        unresolved_high_flag_count=0,
        results=results,
        decision=None,
        decided_at=None,
        decision_note=None,
        pending_review_count=0,
        rubric_is_current=True,
        previous_status=None,
        previous_composite_score=None,
    )
    return ReportExportData(report=report, flags=flags, archive_size_n=42)


def test_sixty_criterion_export_meets_the_speed_and_memory_ac():
    data = _sixty_criterion_report()

    tracemalloc.start()
    start = time.perf_counter()
    pdf_bytes = build_report_pdf(data)
    elapsed = time.perf_counter() - start
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert pdf_bytes.startswith(b"%PDF")
    assert elapsed < _MAX_SECONDS, f"export took {elapsed:.2f}s, AC is <{_MAX_SECONDS}s"
    peak_mb = peak / 1024 / 1024
    assert peak_mb < _MAX_MEMORY_MB, f"export peaked at {peak_mb:.1f}MB, AC is <{_MAX_MEMORY_MB}MB"
