"""BUG-550: a NUL inside a document's own text is dropped at extraction and
counted, so prompts and audit rows built from it can be stored."""

from pathlib import Path

import pymupdf

from app.config import get_settings
from app.ingest import pdf
from app.ingest.normalize import NUL, match_key, normalize
from app.report.page_text import _native_lines


def glyph_zero_pdf(path: Path, text: str, *, link: bool = False) -> Path:
    """One Helvetica line drawn with a raw content stream, so a `\\000` in
    `text` is glyph code 0 -- what PyMuPDF extracts as U+0000. Drawing it
    through insert_text would re-encode the character."""
    content = f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode()
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>",
        b"<</Length %d>>stream\n" % len(content) + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>",
    ]
    body, offsets = b"%PDF-1.4\n", []
    for number, obj in enumerate(objs, 1):
        offsets.append(len(body))
        body += b"%d 0 obj" % number + obj + b"endobj\n"
    xref = len(body)
    body += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    body += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    body += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(body)
    if link:
        with pymupdf.open(path) as doc:
            page = doc[0]
            rect = pymupdf.Rect(page.get_text("blocks")[0][:4])
            page.insert_link(
                {"kind": pymupdf.LINK_URI, "from": rect, "uri": "https://example.org/a"}
            )
            doc.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    return path


def test_normalize_drops_nul_wherever_it_sits():
    assert normalize("Chapter" + NUL + "One") == "ChapterOne"
    assert normalize(NUL + " Intro " + NUL) == "Intro"
    assert normalize(NUL * 3) == ""
    assert match_key("Chap" + NUL + "ter One") == "chapterone"


def test_glyph_zero_pdf_extracts_without_nul_and_records_the_count(tmp_path):
    path = glyph_zero_pdf(
        tmp_path / "glyph_zero.pdf", r"Chapter\000One Introduction The study", link=True
    )
    with pymupdf.open(path) as doc:
        assert NUL in doc[0].get_text()  # the fixture really carries the character

    result = pdf.extract_document(str(path), get_settings())

    assert [block.text for block in result.blocks] == ["ChapterOne Introduction The study"]
    assert [link.text for link in result.uri_links] == ["ChapterOne Introduction The study"]
    # The link reads the same page text, so the character is counted once.
    assert result.nul_characters_removed == 1
    stored = result.model_dump_json()
    assert NUL not in stored and "\\u0000" not in stored
    assert '"nul_characters_removed":1' in stored
    # Anchors stay honest: every source-line range slices the stored text.
    for block in result.blocks:
        for line in block.source_lines or []:
            assert 0 <= line.start < line.end <= len(block.text)
    assert result.text_chars == len("ChapterOne Introduction The study")


def test_report_page_text_reads_the_same_text_as_extraction(tmp_path):
    path = glyph_zero_pdf(tmp_path / "glyph_zero.pdf", r"Chapter\000One Introduction The study")
    extracted = pdf.extract_document(str(path), get_settings())
    with pymupdf.open(path) as doc:
        page = doc[0]
        lines = _native_lines(page, page.get_text("dict", sort=True)["blocks"])
    assert [line.text for line in lines] == [extracted.blocks[0].text]


def test_clean_pdf_keeps_its_stored_bytes(tmp_path):
    path = glyph_zero_pdf(tmp_path / "clean.pdf", "Chapter One Introduction The study")
    result = pdf.extract_document(str(path), get_settings())
    assert result.nul_characters_removed == 0
    assert "nul_characters_removed" not in result.model_dump_json()
