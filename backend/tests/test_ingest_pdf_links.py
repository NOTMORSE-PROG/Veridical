"""PDF URI evidence is bounded local metadata, never a network request."""

import pymupdf
import pytest

from app.config import Settings
from app.ingest.patterns import load_patterns
from app.ingest.pdf import _extract_open_document, extract_document


def build_pdf(path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "References", fontsize=16)
    for y, text in [(100, "https://example."), (120, "org/path.")]:
        page.insert_text((72, y), text, fontsize=11)
        rect = page.search_for(text)[0]
        page.insert_link(
            {"kind": pymupdf.LINK_URI, "from": rect, "uri": "https://example.org/path"}
        )
    doc.save(path)
    doc.close()


def test_pdf_retains_matching_fragments_and_original_text(tmp_path):
    path = tmp_path / "links.pdf"
    build_pdf(path)
    result = extract_document(str(path), Settings(_env_file=None))
    assert result.uri_links_complete is True
    assert [item.text.strip() for item in result.uri_links] == ["https://example.", "org/path."]
    assert {item.uri for item in result.uri_links} == {"https://example.org/path"}
    assert all(item.page == 1 for item in result.uri_links)
    assert "https://example." in "\n".join(block.text for block in result.blocks)


def test_link_budget_does_not_claim_complete_inventory(tmp_path):
    path = tmp_path / "limited.pdf"
    build_pdf(path)
    result = extract_document(str(path), Settings(_env_file=None, ingest_uri_max_links=1))
    assert len(result.uri_links) == 0  # the two-link page cannot fit; no partial page admission
    assert result.uri_links_complete is False


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_cropped_and_rotated_page_uses_visible_unrotated_coordinates(tmp_path, rotation):
    path = tmp_path / "geometry.pdf"
    build_pdf(path)
    with pymupdf.open(path) as doc:
        doc[0].set_cropbox(pymupdf.Rect(60, 60, 550, 780))
        doc[0].set_rotation(rotation)
        result = _extract_open_document(doc, load_patterns(), Settings(_env_file=None))
    assert result.uri_links_complete is True
    assert len(result.uri_links) == 2


def test_exhausted_link_budget_does_not_eagerly_read_any_page_links(tmp_path, monkeypatch):
    path = tmp_path / "many.pdf"
    build_pdf(path)

    def forbidden(*args, **kwargs):
        raise AssertionError("Eager get_links is not a bounded annotation traversal")

    monkeypatch.setattr(pymupdf.Page, "get_links", forbidden)
    result = extract_document(str(path), Settings(_env_file=None, ingest_uri_max_links=1))
    assert len(result.uri_links) == 0
    assert result.uri_links_complete is False
