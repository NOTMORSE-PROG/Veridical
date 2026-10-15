"""Native table detection must not change the manuscript's visible source space."""

import pymupdf
import pytest

from app.config import Settings
from app.ingest.patterns import load_patterns
from app.ingest.pdf import _extract_open_document, _native_tables_in_source_space
from app.ingest.quality import reading_order_issues
from app.ingest.references import extract_references


def document(*, cropped, rotation, table):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((10, 30), "HIDDEN OUTSIDE CROP")
    page.insert_text((72, 88), "References", fontsize=16)
    page.insert_textbox(
        pymupdf.Rect(72, 100, 500, 190),
        "[1] Author A. (2024). Source https://good.\nexample/path.",
        fontsize=11,
        lineheight=1.8,
    )
    page.insert_text((72, 215), "[2] Author B. (2023). Another source.", fontsize=11)
    for fragment in ("https://good.", "example/path."):
        page.insert_link(
            {
                "kind": pymupdf.LINK_URI,
                "from": page.search_for(fragment)[0],
                "uri": "https://good.example/path",
            }
        )
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
    data = doc.tobytes()
    doc.close()
    return pymupdf.open(stream=data, filetype="pdf")


@pytest.mark.parametrize("cropped", [False, True])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("table", [False, True])
@pytest.mark.parametrize("strategy", ["legacy", "native_regions_v1"])
def test_table_detection_preserves_visible_crop_and_source_bound_url(
    cropped, rotation, table, strategy
):
    with document(cropped=cropped, rotation=rotation, table=table) as doc:
        crop_before = tuple(doc[0].cropbox)
        result = _extract_open_document(
            doc, load_patterns(), Settings(_env_file=None, ingest_reading_order_strategy=strategy)
        )
        assert tuple(doc[0].cropbox) == crop_before
        assert doc[0].rotation == rotation
    text = "\n".join(block.text for block in result.blocks)
    if cropped:
        assert "HIDDEN OUTSIDE CROP" not in text
    refs = extract_references(result, load_patterns())
    if strategy == "native_regions_v1" and table:
        # The guarded reader abstains on table layouts, but still preserves
        # the same source/link geometry. The legacy binding controls below
        # remain exercised unchanged for every crop/rotation combination.
        assert refs == []
        assert reading_order_issues(result) == [{"page": 1, "reason": "table_regions_unresolved"}]
    else:
        assert refs[0].url == "https://good.example/path"
    assert {link.uri for link in result.uri_links} == {"https://good.example/path"}
    assert result.uri_links_complete is True
    if table:
        assert len(result.tables) == 1
        assert result.tables[0].rows == [["Name", "Value"], ["Count", "3"]]
        assert result.tables[0].bbox == pytest.approx(
            (12, 180, 212, 220) if cropped else (72, 240, 272, 280)
        )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("failure", ["find", "extract"])
def test_table_failure_still_restores_page_state(monkeypatch, rotation, failure):
    from types import SimpleNamespace

    with document(cropped=True, rotation=rotation, table=True) as doc:
        page = doc[0]
        crop = tuple(page.cropbox)

        def fail():
            raise RuntimeError("table failure control")

        def broken_find(current):
            current.set_cropbox(current.mediabox)
            if failure == "find":
                fail()
            return SimpleNamespace(tables=[SimpleNamespace(bbox=(0, 0, 10, 10), extract=fail)])

        monkeypatch.setattr(pymupdf.Page, "find_tables", broken_find)
        with pytest.raises(RuntimeError, match="failure control"):
            _native_tables_in_source_space(page, 1)
        assert tuple(page.cropbox) == crop
        assert page.rotation == rotation
