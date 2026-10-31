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
from app.ingest.schemas import ExtractionResult, ImageBlock, SectionTree, TextBlock
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
    assert summary.model_dump() == {"checked": 0, "with_content": 0, "not_checked": 3, "no_cue": 3}


def test_only_the_captioned_images_are_read_and_counted(tmp_path):
    path = _pdf_with_images(tmp_path, n_results_images=2)
    settings = get_settings()
    patterns = load_patterns(settings.ingest_patterns_file)
    result = extract_document(str(path), settings)
    # One more image on the same page with no caption.
    result.images.append(ImageBlock(page=2, bbox=(330.0, 600.0, 540.0, 720.0)))
    llm = SpyLLM([{"kind": "table", "confidence": "high", "rows": [["a", "1"]]}, {"kind": "other"}])
    calls = asyncio.run(vision.read_images(result, path, llm, patterns, settings))
    assert calls == 2
    assert [reading.reason for reading in result.image_readings][-1] == "no_table_cue"
    summary = vision.summarize_image_readings(result)
    assert summary.model_dump() == {"checked": 2, "with_content": 1, "not_checked": 1, "no_cue": 1}


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
