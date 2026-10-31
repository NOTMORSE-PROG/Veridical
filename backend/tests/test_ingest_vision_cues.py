"""Only an image with a table or equation cue beside it is sent for reading.

Most thesis images are figures, screenshots and photos, and most tables are
typed text read without a model call, so an uncued image is recorded as not
checked rather than spending a call on it. All synthetic; zero network.
"""

import asyncio
import json

import pytest

from app.config import get_settings
from app.ingest import vision
from app.ingest.image_source import _selected_indices, cued_indices
from app.ingest.patterns import _DEFAULT_FILE, load_patterns
from app.ingest.pdf import extract_document
from app.ingest.schemas import (
    ExtractionResult,
    ImageBlock,
    ImageReading,
    ImageReadingSummary,
    SectionTree,
    TextBlock,
)
from tests.test_ingest_vision import SpyLLM, _pdf_with_images

IMAGE = (100.0, 200.0, 320.0, 320.0)


def _block(text, bbox, *, furniture=False):
    return TextBlock(
        page=1, bbox=bbox, text=text, max_font_size=10, bold_ratio=0, is_furniture=furniture
    )


def _result(*blocks, images=(IMAGE,)):
    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=100,
        section_tree=SectionTree(source="none"),
        blocks=list(blocks),
        images=[ImageBlock(page=1, bbox=bbox) for bbox in images],
    )


def _cued(result):
    return cued_indices(result, load_patterns(), get_settings())


@pytest.mark.parametrize(
    "block, cued",
    [
        (_block("Table 4.1 Mean scores", (100, 180, 300, 192)), True),  # just above
        (_block("TABLE IV Summary of results", (100, 330, 300, 342)), True),  # just below
        (_block("(3.1)", (330, 250, 350, 262)), True),  # equation number beside it
        (_block("Figure 4.1 System architecture", (100, 330, 300, 342)), False),
        (_block("Table 4.1 Mean scores", (100, 60, 300, 72)), False),  # too far above
        (_block("Table 4.1 Mean scores", (400, 180, 560, 192)), False),  # other column
        (_block("Table 4.1 Mean scores", (100, 180, 300, 192), furniture=True), False),
        (_block("Tables in this chapter show", (100, 180, 300, 192)), False),
        (_block("(see above)", (330, 250, 350, 262)), False),
        (_block("(3.1)", (330, 400, 350, 412)), False),  # not level with the image
        (_block("(3.1)", (40, 250, 90, 262)), False),  # left of the image, not its number
        # A tight Word caption touching or slightly overlapping the image edge.
        (_block("Table 2.2: Trade structure", (100, 190, 300, 204)), True),
        (_block("Table 2.2: Trade structure", (100, 316, 300, 330)), True),
        # Mostly inside the image: a label in a picture, not its caption.
        (_block("Table 2.2: Trade structure", (100, 230, 300, 244)), False),
        # Within the overlap, but its middle is inside the image.
        (_block("Table 2.2", (100, 196, 300, 206)), False),
        # Middle above the image, but runs deep into it.
        (_block("Table 2.2: Trade structure", (100, 150, 300, 240)), False),
        # A roman-numeral caption alone in its block, its title on the next line.
        (_block("TABLE IV", (100, 170, 300, 182)), True),
        (_block("Table A.1 Respondent profile", (100, 180, 300, 192)), True),
        # Word's run-together caption numbering, lettered and Filipino captions.
        (_block("Table32.2: Trade structure", (100, 180, 300, 192)), True),
        (_block("Table A-1 Respondent profile", (100, 180, 300, 192)), True),
        (_block("Talahanayan 1 Profile ng mga respondente", (100, 180, 300, 192)), True),
        (_block("Table civil works", (100, 180, 300, 192)), False),
        (_block("Tablet 4 shows", (100, 180, 300, 192)), False),
    ],
)
def test_a_cue_must_be_beside_the_image(block, cued):
    assert _cued(_result(block)) == ({0} if cued else set())


def test_uncaptioned_images_are_recorded_as_not_checked_and_cost_nothing(tmp_path):
    path = _pdf_with_images(tmp_path, n_results_images=3, captioned=False)
    settings = get_settings()
    patterns = load_patterns(settings.ingest_patterns_file)
    result = extract_document(str(path), settings)
    llm = SpyLLM()
    calls = asyncio.run(vision.read_images(result, path, llm, patterns, settings))
    assert calls == 0 and llm.calls == []
    assert result.vision_status == "none"
    assert [reading.reason for reading in result.image_readings] == ["no_table_cue"] * 3
    summary = vision.summarize_image_readings(result)
    assert summary == _summary(not_checked=3, no_cue=3)


def test_only_the_captioned_images_are_read_and_counted(tmp_path):
    path = _pdf_with_images(tmp_path, n_results_images=2)
    settings = get_settings()
    patterns = load_patterns(settings.ingest_patterns_file)
    result = extract_document(str(path), settings)
    # One more image on the same page with no caption.
    result.images.append(ImageBlock(page=2, bbox=(330.0, 600.0, 540.0, 720.0)))
    llm = SpyLLM(
        [
            {"kind": "table", "confidence": "high", "rows": [["a", "1"]]},
            {"kind": "other", "confidence": "high"},
        ]
    )
    calls = asyncio.run(vision.read_images(result, path, llm, patterns, settings))
    assert calls == 2
    assert [reading.reason for reading in result.image_readings][-1] == "no_table_cue"
    summary = vision.summarize_image_readings(result)
    assert summary == _summary(checked=2, with_content=1, not_checked=1, no_cue=1)


def test_the_cap_still_applies_to_cued_images(tmp_path):
    settings = get_settings().model_copy(update={"ingest_vision_max_images": 1})
    path = _pdf_with_images(tmp_path, n_results_images=3)
    result = extract_document(str(path), settings)
    patterns = load_patterns(settings.ingest_patterns_file)
    assert len(cued_indices(result, patterns, settings)) == 3
    assert len(_selected_indices(result, patterns, settings)) == 1
    vision.prepare_image_readings(result, patterns, settings)
    assert sorted(r.reason for r in result.image_readings) == [
        "not_attempted",
        "selection_limit",
        "selection_limit",
    ]


def _summary(**counts):
    return ImageReadingSummary(**{**dict.fromkeys(ImageReadingSummary.model_fields, 0), **counts})


def _reading(index, status, reason=None, attempted=True):
    return ImageReading(image_index=index, status=status, reason=reason, attempted=attempted)


def test_checked_counts_only_readings_that_completed():
    result = _result(images=[IMAGE] * 11)
    result.image_readings = [
        _reading(0, "transcribed"),
        _reading(1, "transcribed", "low_confidence"),
        _reading(2, "classified_only", "content_not_transcribed"),
        _reading(3, "invalid_response", "invalid_response"),
        _reading(4, "failed", "api_down"),
        _reading(5, "failed", "crop_unavailable", attempted=False),
        _reading(6, "not_selected", "no_table_cue", attempted=False),
        _reading(7, "not_selected", "size_filter", attempted=False),
        _reading(8, "not_selected", "selection_limit", attempted=False),
        _reading(9, "not_selected", "unsupported_source", attempted=False),
        _reading(10, "not_selected", "vision_disabled", attempted=False),
    ]
    summary = vision.summarize_image_readings(result)
    # A failed or malformed call never counts as an image checked and found empty.
    assert summary == _summary(
        checked=2,
        with_content=1,
        unclear=2,
        not_checked=7,
        no_cue=1,
        too_small=1,
        unsupported=1,
        over_limit=1,
        unavailable=2,
        not_requested=1,
    )
    reasons = ("no_cue", "too_small", "unsupported", "over_limit", "unavailable", "not_requested")
    assert summary.checked + summary.unclear + summary.not_checked == len(result.images)
    assert sum(getattr(summary, key) for key in reasons) == summary.not_checked


def test_a_failed_call_is_not_reported_as_an_empty_image(tmp_path):
    from app.errors import ApiDownError

    class Down(SpyLLM):
        async def complete(self, *args, **kwargs):
            raise ApiDownError("down")

    path = _pdf_with_images(tmp_path, n_results_images=2)
    settings = get_settings()
    patterns = load_patterns(settings.ingest_patterns_file)
    result = extract_document(str(path), settings)
    with pytest.raises(ApiDownError):
        asyncio.run(vision.read_images(result, path, Down(), patterns, settings))
    summary = vision.summarize_image_readings(result)
    assert summary.checked == 0 and summary.unavailable == 2


def test_the_helper_prepares_crops_for_exactly_the_cued_images(tmp_path):
    """The upload helper and the backend choose with the same code, so a cued
    image always has its crop and an uncued one never costs a render."""
    import pymupdf

    from app.ingest import compute_native

    path = tmp_path / "two.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "Chapter 2 Review of related literature", fontsize=14)
        pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 40, 40), False)
        pix.clear_with(150)
        page.insert_text((100, 192), "Table 2.1 Comparison of systems")
        page.insert_image(pymupdf.Rect(100, 200, 320, 320), pixmap=pix)
        page.insert_image(pymupdf.Rect(100, 500, 320, 620), pixmap=pix)
        doc.save(path)
    settings = get_settings()
    patterns = load_patterns(settings.ingest_patterns_file)
    result = extract_document(str(path), settings)
    picked = set(compute_native._selected_indices(result, patterns, settings))
    assert picked == cued_indices(result, patterns, settings) == {0}


def test_an_extraction_without_readings_has_no_summary():
    assert vision.summarize_image_readings(_result()) is None


def test_a_patterns_file_without_cues_uses_the_shipped_ones(tmp_path):
    shipped = json.loads(_DEFAULT_FILE.read_text("utf-8"))
    shipped.pop("vision_cues")
    custom = tmp_path / "patterns.json"
    custom.write_text(json.dumps(shipped), encoding="utf-8")
    patterns = load_patterns(custom)
    assert patterns.vision_table_caption.match("Table 2 Results")
    assert patterns.vision_caption_gap_pts == load_patterns().vision_caption_gap_pts


@pytest.mark.parametrize("value", [-1, "60", True])
def test_a_bad_cue_distance_is_refused(tmp_path, value):
    shipped = json.loads(_DEFAULT_FILE.read_text("utf-8"))
    shipped["vision_cues"]["caption_gap_pts"] = value
    custom = tmp_path / "patterns.json"
    custom.write_text(json.dumps(shipped), encoding="utf-8")
    with pytest.raises(ValueError, match="caption_gap_pts"):
        load_patterns(custom)


def test_a_patterns_file_naming_some_cues_takes_the_rest_from_the_shipped_file(tmp_path):
    shipped = json.loads(_DEFAULT_FILE.read_text("utf-8"))
    shipped["vision_cues"] = {"caption_gap_pts": 30}
    custom = tmp_path / "patterns.json"
    custom.write_text(json.dumps(shipped), encoding="utf-8")
    patterns = load_patterns(custom)
    assert patterns.vision_caption_gap_pts == 30
    assert patterns.vision_table_caption.match("Table 1")


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_a_distance_that_is_not_finite_is_refused(tmp_path, value):
    text = _DEFAULT_FILE.read_text("utf-8").replace(
        '"caption_gap_pts": 60', f'"caption_gap_pts": {value}'
    )
    custom = tmp_path / "patterns.json"
    custom.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="caption_gap_pts"):
        load_patterns(custom)


@pytest.mark.parametrize(
    "readings, merged, expected",
    [
        ([("transcribed", None), ("classified_only", "content_not_transcribed")], False, True),
        # A single icon too small to read cannot hold a table.
        ([("transcribed", None), ("not_selected", "size_filter")], False, True),
        # An image merged from small pieces can, and it is never read.
        ([("transcribed", None), ("not_selected", "size_filter")], True, False),
        ([("transcribed", None), ("not_selected", "no_table_cue")], False, False),
        ([("transcribed", "low_confidence")], False, False),
        ([("invalid_response", "invalid_response")], False, False),
        ([("failed", "crop_unavailable")], False, False),
        ([("failed", "api_down")], False, False),
        ([("not_selected", "unsupported_source")], False, False),
    ],
)
def test_images_count_as_read_for_tables_only_when_every_one_could_be(readings, merged, expected):
    result = _result(images=[IMAGE] * len(readings))
    if merged:
        result.images[-1] = result.images[-1].model_copy(update={"piece_count": 40})
    result.image_readings = [
        _reading(index, status, reason, attempted=status not in {"not_selected"})
        for index, (status, reason) in enumerate(readings)
    ]
    assert vision.images_read_for_tables(result) is expected


def test_an_extraction_saved_without_readings_leaves_the_rule_to_its_status():
    assert vision.images_read_for_tables(_result()) is None
