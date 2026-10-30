"""BUG-532: touching small PDF image pieces are prepared as one image.

Table rules or a tiled scan drawn as thousands of tiny images used to become
one image per piece, and one real thesis (66,933 pieces) needed more memory
to prepare than the free host has. Small pieces that touch are one picture;
images large enough to be read are never merged, separate pieces stay
separate, and the piece count is kept so nothing disappears from disclosure.
"""

import time

import pymupdf
import pytest

from app.config import Settings
from app.errors import FileTooLargeError
from app.ingest import fragment_guard, pdf
from app.ingest.image_pieces import merge_touching_boxes
from app.ingest.image_source import _selected_indices, prepare_figures, readable_size
from app.ingest.patterns import load_patterns
from app.ingest.schemas import ExtractionResult, ImageBlock
from app.ingest.vision import prepare_image_readings

BUDGET = 10**7


def _settings(tmp_path, **values):
    return Settings(_env_file=None, data_dir=tmp_path, **values)


def _groups(boxes, tolerance=1.0, budget=BUDGET):
    groups, _spent = merge_touching_boxes(boxes, tolerance, budget)
    return groups


def _pix():
    pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 2, 2), False)
    pix.clear_with(90)
    return pix


def _tiled_pdf(path, *, columns, rows, tile=6.0, gap=0.25, extra_figure=True, behind=None):
    """One page holding a picture made of touching small tiles, plus
    (optionally) a separate figure and a large image behind everything."""
    with pymupdf.open() as doc:
        page = doc.new_page(width=612, height=792)
        page.insert_text((40, 40), "Chapter 1 Introduction", fontsize=16)
        pix = _pix()
        if behind is not None:
            page.insert_image(pymupdf.Rect(*behind), pixmap=pix)
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
    assert _groups(boxes) == [
        ((0, 0, 10, 10), [0]),
        ((12, 0, 22, 10), [1]),
        ((0, 12, 10, 22), [2]),
    ]


def test_touching_and_overlapping_boxes_merge_into_their_union():
    boxes = [(0, 0, 10, 10), (10.5, 0, 20, 10), (5, 5, 15, 30)]
    assert _groups(boxes) == [((0, 0, 20, 30), [0, 1, 2])]


def test_a_group_never_swallows_a_box_that_touches_none_of_its_pieces():
    # A header rule meeting a side bar outlines the whole page; a small icon
    # inside that outline touches neither piece and stays its own image.
    boxes = [(0, 0, 100, 1), (99, 0, 100, 100), (40, 50, 50, 60)]
    assert _groups(boxes) == [((0, 0, 100, 100), [0, 1]), ((40, 50, 50, 60), [2])]


def test_the_tolerance_is_the_line_between_touching_and_separate():
    boxes = [(0, 0, 10, 10), (11.5, 0, 20, 10)]
    assert len(_groups(boxes, 1.0)) == 2
    assert len(_groups(boxes, 2.0)) == 1


def test_a_page_of_ten_thousand_tiles_merges_quickly():
    tile, step = 6.0, 6.25
    boxes = [
        (c * step, r * step, c * step + tile, r * step + tile)
        for r in range(100)
        for c in range(100)
    ]
    started = time.perf_counter()
    groups = _groups(boxes)
    assert time.perf_counter() - started < 10
    assert groups == [((0.0, 0.0, 99 * step + tile, 99 * step + tile), list(range(10_000)))]


def test_grouping_stops_at_its_comparison_budget():
    # Full-width strips that never touch keep every strip in the sweep at once.
    strips = [(0, y * 2.0, 500, y * 2.0 + 0.5) for y in range(3000)]
    started = time.perf_counter()
    groups, spent = merge_touching_boxes(strips, 0.1, 100_000)
    assert groups is None and spent > 100_000
    assert time.perf_counter() - started < 5


def test_an_over_budget_page_keeps_its_pieces_and_the_fragment_limit_decides(tmp_path):
    source = _tiled_pdf(tmp_path / "tiled.pdf", columns=12, rows=10)
    settings = _settings(
        tmp_path,
        preparation_image_piece_merge_max_comparisons=10,
        preparation_max_image_fragments=50,
    )
    result = pdf.extract_document(str(source), settings)
    assert len(result.images) == 121
    with pytest.raises(FileTooLargeError, match="121 small image pieces"):
        fragment_guard.refuse_fragment_heavy(result, settings)


def test_the_comparison_budget_is_shared_by_the_whole_document(tmp_path):
    # Two identical tiled pages; the budget covers the first page only, so the
    # second keeps its pieces. A budget that reset per page would merge both.
    tile, step = 6.0, 6.25
    boxes = [
        (40 + c * step, 80 + r * step, 40 + c * step + tile, 80 + r * step + tile)
        for r in range(10)
        for c in range(12)
    ]
    _groups_unused, one_page = merge_touching_boxes(boxes, 1.0, BUDGET)
    path = tmp_path / "two.pdf"
    with pymupdf.open() as doc:
        pix = _pix()
        for _ in range(2):
            page = doc.new_page(width=612, height=792)
            for box in boxes:
                page.insert_image(pymupdf.Rect(*box), pixmap=pix)
        doc.save(str(path))
    settings = _settings(tmp_path, preparation_image_piece_merge_max_comparisons=one_page + 1)
    result = pdf.extract_document(str(path), settings)
    per_page = [sum(1 for image in result.images if image.page == page) for page in (1, 2)]
    assert per_page == [1, 120]


def test_a_tiled_page_becomes_one_image_with_its_piece_count(tmp_path):
    source = _tiled_pdf(tmp_path / "tiled.pdf", columns=12, rows=10)
    result = pdf.extract_document(str(source), _settings(tmp_path))
    assert [image.piece_count for image in result.images] == [120, 1]
    tiled, figure = result.images
    assert tiled.page == figure.page == 1
    assert tiled.bbox == pytest.approx((40.0, 80.0, 40 + 11 * 6.25 + 6, 80 + 9 * 6.25 + 6))
    assert figure.bbox == pytest.approx((100.0, 600.0, 300.0, 760.0))


def test_a_large_image_behind_the_pieces_is_never_merged(tmp_path):
    # A page-sized background under the tiles and the figure overlaps all of
    # them; before this change each was its own image, and each still is.
    source = _tiled_pdf(tmp_path / "behind.pdf", columns=12, rows=10, behind=(0, 0, 612, 792))
    result = pdf.extract_document(str(source), _settings(tmp_path))
    found = sorted((image.piece_count, tuple(image.bbox)) for image in result.images)
    assert len(found) == 3
    assert (1, (0.0, 0.0, 612.0, 792.0)) in found
    assert [count for count, _box in found] == [1, 1, 120]


def test_a_merged_image_is_reported_but_not_read(tmp_path):
    source = _tiled_pdf(tmp_path / "tiled.pdf", columns=12, rows=12)
    settings = _settings(tmp_path)
    result = pdf.extract_document(str(source), settings)
    tiled, figure = result.images
    # Its outline is large enough to read; its pieces were not.
    outline = (tiled.bbox[2] - tiled.bbox[0]) * (tiled.bbox[3] - tiled.bbox[1])
    assert outline > settings.ingest_vision_min_area_pts
    assert not readable_size(tiled, settings) and readable_size(figure, settings)
    patterns = load_patterns(settings.ingest_patterns_file)
    assert _selected_indices(result, patterns, settings) == [1]
    prepare_image_readings(result, patterns, settings)
    assert [reading.reason for reading in result.image_readings] == ["size_filter", "not_attempted"]
    prepare_figures(result, patterns, settings)
    assert result.figure_readings[0].reason == "source_or_relevance_unavailable"


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
        n_images_unassessed=4,
        image_coverage_items=[
            {"image_index": 0, "page": 120, "reason": "size_filter", "piece_count": 9100},
            {"image_index": 1, "page": 121, "reason": "size_filter"},
            {"image_index": 2, "page": 122, "reason": "size_filter", "piece_count": 3},
            {"image_index": 3, "page": 122, "reason": "size_filter"},
        ],
    )
    data = ReportExportData(report=report(coverage), flags=[], archive_size_n=None)
    with pymupdf.open(stream=build_report_pdf(data), filetype="pdf") as document:
        text = " ".join(page.get_text().replace("\n", " ") for page in document)
    assert "(1 image occurrence, stored in the PDF as 9,100 small touching pieces)" in text
    assert "Page 121" in text and "(1 image occurrence)." in text
    # A plain image on the same page is not counted as a piece.
    assert "(2 image occurrences; 1 stored in the PDF as 3 small touching pieces)" in text
    assert (
        "2 listed image occurrences are stored in the PDF as small touching pieces (9,103 in all)"
        in text
    )
