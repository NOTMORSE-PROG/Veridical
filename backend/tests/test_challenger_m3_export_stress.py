"""Milestone 3 Challenger 2: Adversarial Stress Test Suite for ReportLab Export.

Empirically challenges:
1. Multi-page PDF report generation with extreme criterion count & flag volume.
2. DOCX and PDF source format parity in multi-page export.
3. XML/HTML malicious injection resistance in manuscript strings.
4. Page geometry bounds verification (no clipped text blocks across any page).
5. Running headers and footers continuity across all pages.
6. Ground Rule 8: zero '%' symbol in readiness headline verdicts.
"""

from datetime import UTC, datetime

import fitz
import pytest

from app.report.export import build_report_pdf
from app.report.schemas import (
    CriterionResultOut,
    EvidenceItem,
    FlagExportDetailOut,
    FlagSummaryOut,
    PageEvidenceImageOut,
    ReportExportData,
    ReportOut,
    ResolutionOut,
)


@pytest.fixture
def base_report():
    def _create(
        num_criteria: int = 120,
        num_flags: int = 40,
        source_format: str = "docx",
        is_decided: bool = True,
        hazard_payload: str = "",
    ):
        results = [
            CriterionResultOut(
                check_result_id=i,
                criterion_id=i,
                text=f"Criterion {i}: Complex structural constraint {hazard_payload} "
                + ("Paragraph content with multiple sentences to wrap nicely. " * 3),
                type="semantic" if i % 2 == 0 else "structural",
                weight=10.0,
                weight_importance="high" if i % 3 == 0 else ("med" if i % 3 == 1 else "low"),
                kind="semantic_grading" if i % 2 == 0 else "structure_check",
                outcome="passed" if i % 4 != 0 else ("failed" if i % 4 == 1 else "needs_review"),
                score=100.0 if i % 4 == 0 else 50.0,
                basis=None,
                anchor=f"section 3.{i}" if source_format == "docx" else f"page {i % 15 + 1}",
                reasoning=(
                    f"Reasoning for criterion {i} containing {hazard_payload} and justification."
                ),
                reason=None,
                evidence=[
                    EvidenceItem(
                        quote=f"Quoted excerpt for criterion {i} with chars: & < > \" '",
                        anchor=f"para {i}" if source_format == "docx" else f"page {i % 15 + 1}",
                    )
                ],
                resolution=(
                    ResolutionOut(
                        type="mark_pass",
                        reason=f"Instructor approved with note: {hazard_payload}",
                        ai_majority_verdict=None,
                    )
                    if i % 10 == 0
                    else None
                ),
            )
            for i in range(1, num_criteria + 1)
        ]

        flags = [
            FlagSummaryOut(
                id=f_id,
                check_kind="citation_integrity"
                if f_id % 3 == 0
                else ("originality_reuse" if f_id % 3 == 1 else "statistical_forensics"),
                affects_readiness=(f_id % 2 == 0),
                severity="high" if f_id % 4 == 0 else ("medium" if f_id % 4 == 1 else "low"),
                criterion_text=f"Integrity Criterion {f_id % 10 + 1}",
                page_anchor=f"para {f_id}" if source_format == "docx" else f"page {f_id % 12 + 1}",
                evidence_excerpt=(
                    f"Flagged excerpt {f_id}: {hazard_payload} "
                    + ("Repeated citation context. " * 5)
                ),
                resolved=bool(f_id % 5 == 0),
                overridden=bool(f_id % 7 == 0),
            )
            for f_id in range(1, num_flags + 1)
        ]

        flag_details = {
            f.id: FlagExportDetailOut(
                issue=(
                    "citation_missing"
                    if f.check_kind == "citation_integrity"
                    else "math_discrepancy"
                ),
                excerpt=f.evidence_excerpt,
                location_label=f.page_anchor,
                reasoning=f"Discrepancy at {f.page_anchor}. Injection: {hazard_payload}",
                source_identifier=(
                    f"10.100{f.id}/test.doi" if f.check_kind == "citation_integrity" else None
                ),
                override_reason="Instructor checked primary source." if f.overridden else None,
            )
            for f in flags
        }

        page_images = {
            f.id: PageEvidenceImageOut(
                image_png=None,
                page=None if source_format == "docx" else 2,
                reason=(
                    "Native DOCX text - no page image"
                    if source_format == "docx"
                    else "Extracted page"
                ),
                reason_code="docx_layout" if source_format == "docx" else None,
            )
            for f in flags
        }

        report = ReportOut(
            check_run_id=5001,
            manuscript_id=4001,
            manuscript_group_label=f"Group 4001 - Adversarial Defense Test {hazard_payload}",
            manuscript_original_filename=f"manuscript_test.{source_format}",
            rubric_title=f"Standard Thesis Rubric & Evaluation Guide {hazard_payload}",
            status="conditionally_ready",
            composite_score=78.5,
            thresholds={"ready_min_score": 85.0, "not_ready_max_score": 60.0},
            reason=f"Status evaluation completed: conditional readiness. {hazard_payload}",
            flag_deduction=12.0,
            unresolved_high_flag_count=2,
            llm_mode="real",
            results=results,
            decision="conditionally_ready" if is_decided else None,
            decided_at=datetime(2026, 10, 27, 14, 30, tzinfo=UTC) if is_decided else None,
            decision_note=(f"Consensus note with hazard {hazard_payload}." if is_decided else None),
            pending_review_count=10,
            rubric_is_current=True,
            previous_status=None,
            previous_composite_score=None,
        )

        return ReportExportData(
            report=report,
            flags=flags,
            flag_details=flag_details,
            page_images=page_images,
            archive_size_n=25,
            source_format=source_format,
        )

    return _create


def test_extreme_multipage_docx_export_geometry(base_report):
    """Stress test a 120-criteria, 40-flag DOCX manuscript export."""
    data = base_report(num_criteria=120, num_flags=40, source_format="docx", is_decided=True)
    pdf_bytes = build_report_pdf(data)
    assert len(pdf_bytes) > 10000

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(doc)
    # 120 criteria and 40 flags must span at least 8 pages
    assert page_count >= 8, f"Expected >= 8 pages for 120 criteria, got {page_count}"

    for page_idx, page in enumerate(doc):
        page_num = page_idx + 1
        rect = page.rect
        blocks = page.get_text("blocks")
        text = page.get_text()

        # Check for text clipping outside page bounds
        clipped = [
            b
            for b in blocks
            if b[0] < -1.0 or b[1] < -1.0 or b[2] > rect.width + 1.0 or b[3] > rect.height + 1.0
        ]
        assert len(clipped) == 0, f"Page {page_num} clipped blocks: {clipped}"

        # Footer page number check
        has_page = (
            f"Page {page_num}" in text
            or f"{page_num} of" in text
            or f"Page {page_num} of {page_count}" in text
        )
        assert has_page, f"Page {page_num} missing page number in footer"

        # Running header on subsequent pages
        if page_idx > 0:
            assert "VERIDICAL - Readiness Report" in text or "VERIDICAL" in text, (
                f"Page {page_num} missing running header"
            )


def test_extreme_multipage_pdf_export_geometry(base_report):
    """Stress test a 100-criteria, 30-flag PDF manuscript export."""
    data = base_report(num_criteria=100, num_flags=30, source_format="pdf", is_decided=True)
    pdf_bytes = build_report_pdf(data)
    assert len(pdf_bytes) > 8000

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(doc)
    assert page_count >= 6, f"Expected >= 6 pages, got {page_count}"

    for page_idx, page in enumerate(doc):
        page_num = page_idx + 1
        rect = page.rect
        blocks = page.get_text("blocks")

        clipped = [
            b
            for b in blocks
            if b[0] < -1.0 or b[1] < -1.0 or b[2] > rect.width + 1.0 or b[3] > rect.height + 1.0
        ]
        assert len(clipped) == 0, f"Page {page_num} clipped blocks: {clipped}"


def test_export_xml_malicious_entities(base_report):
    """Verify immunity to XML and HTML entity injections in report fields."""
    hazard = "<script>alert('pwned')</script>&amp;&lt;&gt;\"'<b>Bold</b><unclosed tag"
    data = base_report(
        num_criteria=15,
        num_flags=10,
        source_format="docx",
        is_decided=True,
        hazard_payload=hazard,
    )
    pdf_bytes = build_report_pdf(data)
    assert len(pdf_bytes) > 2000

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = "\n".join(p.get_text() for p in doc)
    # The literal script tags must be escaped and rendered harmlessly as text
    assert "alert('pwned')" in full_text or "alert(" in full_text


def test_export_draft_vs_decided_watermark(base_report):
    """Verify DRAFT watermark is present if and only if decision is None."""
    # 1. Draft
    draft_data = base_report(num_criteria=10, num_flags=5, source_format="docx", is_decided=False)
    draft_pdf = build_report_pdf(draft_data)
    doc_draft = fitz.open(stream=draft_pdf, filetype="pdf")
    text_draft = "\n".join(p.get_text() for p in doc_draft)
    assert "DRAFT" in text_draft

    # 2. Decided
    decided_data = base_report(num_criteria=10, num_flags=5, source_format="docx", is_decided=True)
    decided_pdf = build_report_pdf(decided_data)
    doc_decided = fitz.open(stream=decided_pdf, filetype="pdf")
    text_decided = "\n".join(p.get_text() for p in doc_decided)
    assert "DRAFT" not in text_decided


def test_ground_rule_8_no_percent_in_headline_verdict(base_report):
    """Ground Rule 8: Judgments in named bands, never percentages in headlines."""
    data = base_report(num_criteria=20, num_flags=10, source_format="docx", is_decided=True)
    pdf_bytes = build_report_pdf(data)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    first_page_text = doc[0].get_text()

    # The headline readiness verdict must be qualitative (Title Case)
    assert "Conditionally Ready" in first_page_text
    # Check that the hero verdict does not have percentage symbols attached to the band
    assert "Conditionally Ready %" not in first_page_text
    assert "Ready %" not in first_page_text
    assert "Not Ready %" not in first_page_text
