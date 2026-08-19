"""V-065 Q2 tests: anchor-to-region recovery (`app/ingest/regions.py`).

Synthetic PDF, same `PdfBuilder` convention as `test_ingest_pdf.py` — no
binary fixtures, deterministic. The real-document measurement this module's
design rests on (13/13 hit for quoted-prose flags, 0/12 for synthesized
excerpts, against the real 47-page manuscript) lives in this session's
scratchpad, not committed — these tests lock in the *mechanism* the
measurement validated, on a document this repo actually owns.
"""

from pathlib import Path

import pymupdf
import pytest

from app.config import get_settings
from app.ingest.pdf import extract_document
from app.ingest.regions import AnchorRegion, recover_region
from app.ingest.schemas import SectionTree
from tests.test_ingest_pdf import PdfBuilder


def _regions_pdf(tmp_path: Path) -> Path:
    """2 chapters + a references section, real quoted prose on chapter
    pages so a genuine excerpt is verbatim-recoverable."""
    b = PdfBuilder()
    b.new_page().line("CHAPTER 1 INTRODUCTION", bold=True)
    b.line("Before a student can defend their capstone the format must be checked.")
    b.new_page().line("CHAPTER 2 METHODS", bold=True)
    b.line("This chapter presents how the tool was planned and developed.")
    b.new_page().line("CHAPTER 3 RESULTS", bold=True)
    b.line("Results are discussed in this chapter at length.")
    b.new_page().line("REFERENCES", bold=True)
    b.line("Reyes, J. P., & Cruz, M. A. (2023). Assessing capstone readiness.")
    b.line("Garcia, L. (2020). Understanding rubric design. Academic Press.")
    return b.save(tmp_path / "regions.pdf")


@pytest.fixture(scope="module")
def regions_doc(tmp_path_factory):
    path = _regions_pdf(tmp_path_factory.mktemp("pdfs"))
    extraction = extract_document(str(path), get_settings())
    doc = pymupdf.open(str(path))
    yield doc, extraction.section_tree
    doc.close()


def test_page_anchor_with_real_quote_recovers_bbox(regions_doc):
    doc, tree = regions_doc
    region = recover_region(
        doc, tree, "p. 1", "Before a student can defend their capstone the format must be checked."
    )
    assert region.kind == "page_bbox"
    assert region.page == 1
    assert region.bbox is not None


def test_bbox_is_returned_in_raw_pdf_space_not_mupdf_space(regions_doc):
    """Found live (`ux-critic`, 2026-08-19): every highlight rendered ~one
    text-line above its real target, because `page.search_for()`'s own
    coordinates (MuPDF convention: origin top-left, y DOWNWARD) were fed
    straight into pdf.js's `convertToViewportPoint`, which expects raw PDF
    points (origin bottom-left, y UPWARD). This asserts the actual
    geometry, not just "a bbox exists" -- proven against the pre-fix code
    as a genuine negative control (temporarily reverted the flip, this
    test failed; every other test in this file still passed, confirming
    THIS is the test that catches the regression the others don't)."""
    doc, tree = regions_doc
    page = doc[0]
    page_height = page.rect.height

    region = recover_region(
        doc, tree, "p. 1", "Before a student can defend their capstone the format must be checked."
    )
    assert region.bbox is not None
    _, y0, _, y1 = region.bbox

    # The fixture's body line sits within the first ~110pt from the TOP of
    # the page in MuPDF's own layout coordinates (PdfBuilder starts at
    # MARGIN_X=72, LINE_STEP=18). Correctly flipped to raw PDF space (y
    # increases UPWARD from the bottom), that same text must land in the
    # UPPER half of the page -- i.e. well above page_height / 2. Still in
    # MuPDF's unflipped convention, it would instead be a small value near
    # 0, in the page's lower half by this same test.
    assert y0 > page_height / 2 and y1 > page_height / 2, (
        f"bbox y=({y0}, {y1}) on a {page_height}pt-tall page reads as the LOWER "
        "half of the page in PDF space -- text this close to the fixture's top "
        "margin must land in the upper half once correctly flipped; if this "
        "fails, the MuPDF->PDF y-flip regressed and every highlight is off by "
        "roughly one line again"
    )


def test_page_anchor_with_synthesized_excerpt_is_page_only_not_fabricated(regions_doc):
    """The excerpt was never on the page as written (a computed summary,
    e.g. a statistical-forensics flag) — must NOT return a fake box."""
    doc, tree = regions_doc
    region = recover_region(doc, tree, "p. 1", "n=5, M=4.20, SD=0.37 (Instructors)")
    assert region.kind == "page_only"
    assert region.page == 1
    assert region.bbox is None


def test_page_anchor_out_of_range_is_unresolved(regions_doc):
    doc, tree = regions_doc
    region = recover_region(doc, tree, "p. 999", "anything")
    assert region == AnchorRegion(kind="unavailable")


def test_reference_list_anchor_resolves_to_references_page(regions_doc):
    doc, tree = regions_doc
    region = recover_region(doc, tree, "reference list", "Reyes, J. P., & Cruz, M. A. (2023)...")
    assert region.kind == "reference_list"
    assert region.page == 4


def test_reference_index_anchor_resolves_coarsely_to_same_page(regions_doc):
    """Documented limitation: per-entry precision isn't built — every
    reference index anchor resolves to the reference section's own page."""
    doc, tree = regions_doc
    region = recover_region(doc, tree, "reference #2", "Garcia, L. (2020)...")
    assert region.kind == "reference_position"
    assert region.page == 4
    assert region.index == 2


def test_whole_document_anchor_spans_the_full_document(regions_doc):
    doc, tree = regions_doc
    region = recover_region(doc, tree, "whole document", "irrelevant excerpt")
    assert region.kind == "whole_document"
    assert region.page == 1
    assert region.end_page == doc.page_count


def test_chapter_title_anchor_resolves_to_its_own_span(regions_doc):
    doc, tree = regions_doc
    region = recover_region(doc, tree, "CHAPTER 2 METHODS", "irrelevant excerpt")
    assert region.kind == "section"
    assert region.page == 2
    assert region.end_page == 2


def test_unknown_chapter_title_anchor_is_unresolved(regions_doc):
    doc, tree = regions_doc
    region = recover_region(doc, tree, "CHAPTER 99 NOWHERE", "irrelevant excerpt")
    assert region == AnchorRegion(kind="unavailable")


def test_paragraph_anchor_resolves_directly_no_pdf_needed():
    """DOCX manuscripts (V-065 Q1: no PDF.js pane, no pages at all) pass
    doc=None -- the paragraph index comes straight from the anchor
    string, no PDF operation involved."""
    region = recover_region(None, SectionTree(source="none", nodes=[]), "¶4", "irrelevant")
    assert region.kind == "paragraph_only"
    assert region.paragraph == 4


def test_page_anchor_with_no_doc_is_unresolved_not_a_crash():
    region = recover_region(None, SectionTree(source="none", nodes=[]), "p. 3", "irrelevant")
    assert region == AnchorRegion(kind="unavailable")


def test_whole_document_anchor_with_no_doc_has_no_page_range():
    region = recover_region(None, SectionTree(source="none", nodes=[]), "whole document", "x")
    assert region.kind == "whole_document"
    assert region.page is None
    assert region.end_page is None


def test_search_falls_back_to_shorter_candidates_when_the_full_excerpt_wont_match(regions_doc):
    """A real stored excerpt can carry trailing text (truncation, adjacent
    sentence bleed) that never appears on the page — the full-length and
    mid-length candidates should all fail to match, and only the shortest
    (a clean word-boundary-adjacent prefix) recovers a box."""
    doc, tree = regions_doc
    excerpt = (
        "Before a student can defend their capstone the format must be checked. "
        "XXXXXXXXXX this trailing text never appears anywhere in the document XXXXXXXXXX"
    )
    region = recover_region(doc, tree, "p. 1", excerpt)
    assert region.kind == "page_bbox"
    assert region.page == 1
