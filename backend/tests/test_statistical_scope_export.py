"""Render all saved F6 states, including reports without a finding."""

import pymupdf
import pytest

from app.report.export import build_report_pdf
from app.report.schemas import ReportExportData
from tests.test_statistical_scope_surfaces import scope_report


@pytest.mark.parametrize(
    ("state", "label", "qualification"),
    [
        ("completed", "Statistical checks: Completion recorded.", "verify the raw data"),
        ("not_applicable", "Statistical checks: Not applicable.", "contains no statistics"),
        ("incomplete", "Statistical checks: Not fully assessed.", "not treated as passed"),
        ("not_recorded", "Statistical checks: Not recorded.", "Do not infer"),
    ],
)
def test_pdf_keeps_status_and_qualification_when_no_flag_exists(
    tmp_path, state, label, qualification
):
    report = scope_report(state)
    pdf = build_report_pdf(ReportExportData(report=report, flags=[], archive_size_n=0))
    (tmp_path / f"scope-{state}.pdf").write_bytes(pdf)
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        text = " ".join(" ".join(page.get_text().split()) for page in document)
    assert text.count(label) == 1
    assert qualification in text
    assert "private_prompt" not in text
