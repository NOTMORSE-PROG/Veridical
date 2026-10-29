"""BUG-532: touching PDF image pieces are prepared as one image.

A tiled scan or a chart cut into strips used to become one image per piece,
and one real thesis (66,933 pieces) needed more memory to prepare than the
free host has. Pieces that touch are one picture; separate figures stay
separate, and the piece count is kept so nothing disappears from disclosure.
"""

import time

import pymupdf
import pytest

from app.config import Settings
from app.errors import FileTooLargeError
from app.ingest import fragment_guard, pdf
from app.ingest.image_pieces import merge_touching_boxes
from app.ingest.schemas import ExtractionResult, ImageBlock


def _settings(tmp_path, **values):
    return Settings(_env_file=None, data_dir=tmp_path, **values)


def _tiled_pdf(path, *, columns, rows, tile=6.0, gap=0.25, extra_figure=True):
    """One page holding a picture made of touching tiles, like a tiled scan,
    plus (optionally) a separate figure well away from it."""
    with pymupdf.open() as doc:
        page = doc.new_page(width=612, height=792)
        page.insert_text((40, 40), "Chapter 1 Introduction", fontsize=16)
        pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 2, 2), False)
        pix.clear_with(90)
        for row in range(rows):
            for column in range(columns):
                x = 40 + column * (tile + gap)
                y = 80 + row * (tile + gap)
                page.insert_image(pymupdf.Rect(x, y, x + tile, y + tile), pixmap=pix)
        if extra_figure:
            page.insert_image(pymupdf.Rect(100, 600, 300, 760), pixmap=pix)
        doc.save(str(path))
    return path


def test_separate_boxes_stay_separate():
    boxes = [(0, 0, 10, 10), (12, 0, 22, 10), (0, 12, 10, 22)]
    assert merge_touching_boxes(boxes, 1.0) == [
        ((0, 0, 10, 10), [0]),
        ((12, 0, 22, 10), [1]),
        ((0, 12, 10, 22), [2]),
    ]


def test_touching_and_overlapping_boxes_merge_into_their_union():
    boxes = [(0, 0, 10, 10), (10.5, 0, 20, 10), (5, 5, 15, 30)]
    assert merge_touching_boxes(boxes, 1.0) == [((0, 0, 20, 30), [0, 1, 2])]


def test_a_box_covered_only_by_the_grown_union_joins_it():
    # An L of two touching boxes grows a union that covers the third box,
    # which touches neither piece on its own.
    boxes = [(0, 0, 100, 10), (90, 0, 100, 100), (40, 50, 50, 60)]
    assert merge_touching_boxes(boxes, 1.0) == [((0, 0, 100, 100), [0, 1, 2])]


def test_the_tolerance_is_the_line_between_touching_and_separate():
    boxes = [(0, 0, 10, 10), (11.5, 0, 20, 10)]
    assert len(merge_touching_boxes(boxes, 1.0)) == 2
    assert len(merge_touching_boxes(boxes, 2.0)) == 1


def test_a_page_of_ten_thousand_tiles_merges_quickly():
    tile, step = 6.0, 6.25
    boxes = [
        (c * step, r * step, c * step + tile, r * step + tile)
        for r in range(100)
        for c in range(100)
    ]
    started = time.perf_counter()
    groups = merge_touching_boxes(boxes, 1.0)
    assert time.perf_counter() - started < 10
    assert groups == [((0.0, 0.0, 99 * step + tile, 99 * step + tile), list(range(10_000)))]


def test_a_tiled_page_becomes_one_image_with_its_piece_count(tmp_path):
    source = _tiled_pdf(tmp_path / "tiled.pdf", columns=12, rows=10)
    result = pdf.extract_document(str(source), _settings(tmp_path))
    assert [image.piece_count for image in result.images] == [120, 1]
    tiled, figure = result.images
    assert tiled.page == figure.page == 1
    assert tiled.bbox == pytest.approx((40.0, 80.0, 40 + 11 * 6.25 + 6, 80 + 9 * 6.25 + 6))
    assert figure.bbox == pytest.approx((100.0, 600.0, 300.0, 760.0))


def test_turning_merging_off_keeps_every_piece(tmp_path):
    source = _tiled_pdf(tmp_path / "tiled.pdf", columns=12, rows=10)
    result = pdf.extract_document(
        str(source), _settings(tmp_path, preparation_merge_touching_image_pieces=False)
    )
    assert len(result.images) == 121
    assert {image.piece_count for image in result.images} == {1}


def test_a_tiled_scan_is_no_longer_refused_as_too_many_pieces(tmp_path):
    source = _tiled_pdf(tmp_path / "tiled.pdf", columns=12, rows=10)
    settings = _settings(tmp_path, preparation_max_image_fragments=50)
    fragment_guard.refuse_fragment_heavy(pdf.extract_document(str(source), settings), settings)
    unmerged = settings.model_copy(update={"preparation_merge_touching_image_pieces": False})
    with pytest.raises(FileTooLargeError, match="121 small image pieces"):
        fragment_guard.refuse_fragment_heavy(pdf.extract_document(str(source), unmerged), unmerged)


def test_an_unmerged_image_is_stored_exactly_as_before():
    image = ImageBlock(page=3, bbox=(1.0, 2.0, 3.0, 4.0))
    assert "piece_count" not in image.model_dump(mode="json")
    assert "piece_count" not in image.model_dump_json()
    merged = ImageBlock(page=3, bbox=(1.0, 2.0, 3.0, 4.0), piece_count=7)
    assert merged.model_dump(mode="json")["piece_count"] == 7
    assert ImageBlock.model_validate_json(merged.model_dump_json()).piece_count == 7


def test_a_stored_extraction_without_piece_counts_still_loads(tmp_path):
    source = _tiled_pdf(tmp_path / "one.pdf", columns=1, rows=1, extra_figure=False)
    stored = pdf.extract_document(str(source), _settings(tmp_path)).model_dump(mode="json")
    assert stored["images"] and all("piece_count" not in image for image in stored["images"])
    assert ExtractionResult.model_validate(stored).images[0].piece_count == 1


def _coverage_extraction(images):
    from app.ingest.schemas import SectionTree, TextBlock

    return ExtractionResult(
        page_count=2,
        anchor_kind="page",
        image_only=False,
        text_chars=24,
        section_tree=SectionTree(source="none"),
        blocks=[TextBlock(page=1, text="Qualitative description.", max_font_size=11, bold_ratio=0)],
        images=images,
    )


def test_image_coverage_reports_the_pieces_inside_a_merged_image():
    from app.checks.forensics.coverage import image_coverage

    detail = image_coverage(
        _coverage_extraction(
            [
                ImageBlock(page=1, bbox=(0, 0, 400, 500), piece_count=544),
                ImageBlock(page=2, bbox=(0, 0, 100, 100)),
            ]
        ),
        [],
    )
    assert detail["n_image_occurrences"] == 2
    assert detail["n_image_pieces"] == 545
    first, second = detail["image_coverage_items"]
    assert first["piece_count"] == 544
    assert "piece_count" not in second


def test_image_coverage_of_unmerged_images_is_unchanged():
    from app.checks.forensics.coverage import image_coverage

    detail = image_coverage(_coverage_extraction([ImageBlock(page=1, bbox=(0, 0, 9, 9))]), [])
    assert "n_image_pieces" not in detail
    assert detail["image_coverage_items"] == [
        {"image_index": 0, "page": 1, "paragraph": None, "reason": "reading_not_recorded"}
    ]


def test_the_export_says_how_many_pieces_an_unassessed_image_holds():
    from app.report.export import build_report_pdf
    from app.report.schemas import IntegrityCheckStatusOut, ReportExportData
    from tests.test_partial_coverage_reports import report

    coverage = IntegrityCheckStatusOut(
        check_kind="statistical_forensics",
        outcome="escalated",
        n_skipped_quota=0,
        n_skipped_api_down=0,
        n_skipped_parse_failure=0,
        coverage_reason="image_content_unassessed",
        assessment_scope="available_text_and_tables",
        analysis_coverage="partial",
        n_images_unassessed=2,
        image_coverage_items=[
            {"image_index": 0, "page": 120, "reason": "size_filter", "piece_count": 9100},
            {"image_index": 1, "page": 121, "reason": "size_filter"},
        ],
    )
    data = ReportExportData(report=report(coverage), flags=[], archive_size_n=None)
    with pymupdf.open(stream=build_report_pdf(data), filetype="pdf") as document:
        text = " ".join(page.get_text().replace("\n", " ") for page in document)
    assert "(1 image occurrence, made of 9,100 touching pieces)" in text
    assert "Page 121" in text and "(1 image occurrence)." in text
