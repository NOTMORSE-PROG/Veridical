"""Printed source captions use the same visible positions as the report."""

import pymupdf
import pytest
from reportlab.platypus import SimpleDocTemplate

from app.report.export import _PRINT_LAYOUT, _register_fonts, _styles
from app.report.schemas import PageEvidenceImageOut, SourceEvidencePrintOut
from app.report.source_print import source_caption, source_evidence_flowables


@pytest.mark.parametrize(
    "anchor,location",
    [
        ("paragraph 0", "Extracted paragraph 1"),
        ("paragraph 3", "Extracted paragraph 4"),
        ("¶9", "Extracted paragraph 10"),
        ("page 2", "page 2"),
        ("Section 2.3", "Section 2.3"),
        (None, "Not recorded"),
    ],
)
def test_caption_converts_only_canonical_paragraph_positions(anchor, location):
    assert f"Saved location: {location}." in source_caption("Source", anchor, "Qualification")


def test_rendered_docx_evidence_caption_matches_visible_paragraph_position(tmp_path):
    entry = SourceEvidencePrintOut(
        reference_id="a" * 64,
        criterion_id=51,
        flag_id=None,
        role="criterion_evidence",
        index=0,
        quote="Objectives of the Study",
        anchor="paragraph 3",
        placement_state="paragraph_only",
        image=PageEvidenceImageOut(
            image_png=None, page=None, reason="The saved source uses extracted paragraph positions."
        ),
    )
    path = tmp_path / "source-position.pdf"
    document = SimpleDocTemplate(str(path))
    document.build(
        source_evidence_flowables(
            entry, document.width, _styles(_register_fonts()), 72, _PRINT_LAYOUT
        )
    )
    with pymupdf.open(path) as pdf:
        text = " ".join(" ".join(page.get_text().split()) for page in pdf)
    assert "Objectives of the Study" in text
    assert "Saved location: Extracted paragraph 4." in text
    assert entry.anchor == "paragraph 3"
