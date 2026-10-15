"""Serialized PDF controls for source coordinates, independent of citation parsing."""

import pymupdf
import pytest

from app.config import Settings
from app.ingest.patterns import load_patterns
from app.ingest.pdf import _extract_open_document, _native_tables_in_source_space
from app.ingest.schemas import ExtractionResult


@pytest.mark.parametrize("cropped", [False, True])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("table", [False, True])
def test_serialized_native_extraction_preserves_source_space(cropped, rotation, table):
    original = pymupdf.open()
    page = original.new_page()
    page.insert_text((10, 30), "OUTSIDE VISIBLE CROP")
    page.insert_text((72, 100), "Visible manuscript evidence")
    if table:
        for row, cells in enumerate([["Name", "Value"], ["Count", "3"]]):
            for column, text in enumerate(cells):
                page.insert_text((77 + 100 * column, 255 + 20 * row), text, fontsize=9)
        for x in (72, 172, 272):
            page.draw_line((x, 240), (x, 280))
        for y in (240, 260, 280):
            page.draw_line((72, y), (272, y))
    if cropped:
        page.set_cropbox(pymupdf.Rect(60, 60, 550, 780))
    page.set_rotation(rotation)
    data = original.tobytes()
    original.close()
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        crop = tuple(doc[0].cropbox)
        result = _extract_open_document(doc, load_patterns(), Settings(_env_file=None))
        assert tuple(doc[0].cropbox) == crop
        assert doc[0].rotation == rotation
        assert result.geometry[0].rotation == rotation
    text = "\n".join(block.text for block in result.blocks)
    assert "Visible manuscript evidence" in text
    if cropped:
        assert "OUTSIDE VISIBLE CROP" not in text
    if table:
        assert len(result.tables) == 1
        assert result.tables[0].rows == [["Name", "Value"], ["Count", "3"]]
        assert result.tables[0].bbox == pytest.approx(
            (12, 180, 212, 220) if cropped else (72, 240, 272, 280)
        )
    reloaded = ExtractionResult.model_validate_json(result.model_dump_json())
    assert reloaded == result


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("failure", ["find", "extract"])
def test_native_table_failure_restores_source_state(monkeypatch, rotation, failure):
    from types import SimpleNamespace

    with pymupdf.open() as doc:
        page = doc.new_page()
        page.set_cropbox(pymupdf.Rect(60, 60, 550, 780))
        page.set_rotation(rotation)
        crop = tuple(page.cropbox)

        def fail():
            raise RuntimeError("table failure control")

        def broken_find(current):
            current.set_cropbox(current.mediabox)
            if failure == "find":
                fail()
            return SimpleNamespace(tables=[SimpleNamespace(bbox=(0, 0, 10, 10), extract=fail)])

        monkeypatch.setattr(pymupdf.Page, "find_tables", broken_find)
        with pytest.raises(RuntimeError, match="table failure control"):
            _native_tables_in_source_space(page, 1)
        assert tuple(page.cropbox) == crop
        assert page.rotation == rotation
