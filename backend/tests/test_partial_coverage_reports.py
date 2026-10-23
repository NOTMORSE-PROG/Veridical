"""F8 partial coverage is preserved without exposing private report detail."""

import pymupdf
import pytest

from app.report.export import build_report_pdf
from app.report.schemas import IntegrityCheckStatusOut, ReportExportData, ReportOut
from app.share.service import _to_public_report


def report(coverage):
    return ReportOut(
        check_run_id=1,
        manuscript_id=1,
        manuscript_group_label="Controlled sample",
        manuscript_original_filename="sample.pdf",
        rubric_title="Example rubric",
        status="needs_review",
        composite_score=None,
        thresholds={"ready_min_score": 85, "not_ready_max_score": 60},
        reason="Incomplete assessment",
        flag_deduction=0,
        unresolved_high_flag_count=0,
        decision=None,
        decided_at=None,
        decision_note=None,
        pending_review_count=0,
        rubric_is_current=True,
        llm_mode="real",
        previous_status=None,
        previous_composite_score=None,
        results=[],
        integrity_check_status=[coverage] if coverage else [],
    )


def status(state="partial"):
    return IntegrityCheckStatusOut(
        check_kind="citation_integrity",
        outcome="escalated",
        n_skipped_quota=0,
        n_skipped_api_down=0,
        n_skipped_parse_failure=0,
        coverage_reason="source_reading_order_partial",
        analysis_coverage=state,
        assessment_scope="reference_metadata_only",
        n_reference_metadata_attempted=1,
        n_source_pages_unassessed=3,
    )


@pytest.mark.parametrize("state", ["partial", "unsupported"])
def test_public_report_discloses_incomplete_assessment_without_private_detail(state):
    public = _to_public_report(report(status(state))).model_dump()
    assert public["assessment_incomplete"] is True
    assert "integrity_check_status" not in public
    assert "assessment_scope" not in public
    assert "source_issues" not in public
    assert "pending_review_count" not in public


def test_public_report_does_not_manufacture_incomplete_integrity_assessment():
    assert _to_public_report(report(None)).assessment_incomplete is False


@pytest.mark.parametrize(
    "state, label", [("partial", "Partially assessed"), ("unsupported", "Not assessed")]
)
def test_export_keeps_partial_metadata_meaning_not_whole_manuscript_pass(state, label):
    data = ReportExportData(report=report(status(state)), flags=[], archive_size_n=None)
    with pymupdf.open(stream=build_report_pdf(data), filetype="pdf") as document:
        text = " ".join(page.get_text().replace("\n", " ") for page in document)
    assert "Assessment limits" in text
    assert label in text
    assert "Citation matching and claim support across the manuscript were not assessed" in text
    assert "1 reference checks attempted" in text
    assert "No integrity flags were recorded" in text
    assert "source reading order partial" not in text


def test_export_retains_metadata_provider_outage_count():
    coverage = status().model_copy(update={"n_metadata_api_down": 1})
    data = ReportExportData(report=report(coverage), flags=[], archive_size_n=None)
    with pymupdf.open(stream=build_report_pdf(data), filetype="pdf") as document:
        text = " ".join(page.get_text().replace("\n", " ") for page in document)
    assert "1 source checks could not reach an external service" in text


@pytest.mark.parametrize("state", ["partial", "unsupported"])
def test_saved_image_coverage_survives_export_and_public_incomplete_notice(state):
    coverage = IntegrityCheckStatusOut(
        check_kind="statistical_forensics",
        outcome="escalated",
        n_skipped_quota=0,
        n_skipped_api_down=0,
        n_skipped_parse_failure=0,
        coverage_reason="image_content_unassessed",
        assessment_scope="available_text_and_tables",
        analysis_coverage=state,
        n_images_unassessed=2,
        n_equations_unassessed=1,
        image_coverage_items=[
            {"image_index": 0, "paragraph": 4, "reason": "unsupported_source"},
            {"image_index": 1, "paragraph": 4, "reason": "unsupported_source"},
        ],
    )
    saved_report = report(coverage)
    assert _to_public_report(saved_report).assessment_incomplete is True
    data = ReportExportData(report=saved_report, flags=[], archive_size_n=None)
    with pymupdf.open(stream=build_report_pdf(data), filetype="pdf") as document:
        text = " ".join(page.get_text().replace("\n", " ") for page in document)
    assert "2 image occurrences unassessed" in text
    assert "1 equation transcribed but not evaluated" in text
    assert (
        text.count("Extracted paragraph 5: Image assessment is not supported for this source") == 1
    )
    assert "(2 image occurrences)" in text
    assert "These are coverage limitations, not findings against the manuscript" in text
    if state == "partial":
        assert "Statistical statements were extracted from readable text or tables" in text
    else:
        assert "Unassessed images may contain information this check missed" in text
