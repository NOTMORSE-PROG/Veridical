"""Regenerate the committed ingestion fixture corpus (V-008).

    uv run python -m tests.fixtures.ingest.build_fixtures

The binaries ARE committed (they are the CI regression gate, TESTING.md §1
"fixture suite"); this script exists so a fixture can be rebuilt
deliberately — never silently. Expected outputs live next to each fixture
as <name>.expected.json and are asserted by test_ingest_fixtures.py.
"""

import shutil
from pathlib import Path

import pymupdf

from tests.test_ingest_docx import _docx_with_styles
from tests.test_ingest_pdf import PdfBuilder, _thesis_pdf

FIXTURE_DIR = Path(__file__).parent


def _image_table_pdf() -> None:
    """A normal-looking document whose results table exists only as an
    embedded image — the F1.3 case."""
    b = PdfBuilder()
    b.new_page().line("CHAPTER 1 INTRODUCTION", bold=True)
    for i in range(8):
        b.line(f"Introductory prose sentence {i} that describes the study in detail.")
    b.new_page().line("CHAPTER 4 RESULTS AND DISCUSSION", bold=True)
    for i in range(6):
        b.line(f"Discussion of the findings, sentence {i}, referring to Table 4.2 below.")
    b.image(rect=(100, 300, 400, 450))
    b.save(FIXTURE_DIR / "image_table.pdf")


def build() -> None:
    _thesis_pdf(FIXTURE_DIR).replace(FIXTURE_DIR / "native.pdf")  # replace: idempotent on Windows
    _docx_with_styles(FIXTURE_DIR / "native.docx")
    _image_table_pdf()

    # A DOCX wearing a .pdf extension — content sniffing must see through it.
    shutil.copyfile(FIXTURE_DIR / "native.docx", FIXTURE_DIR / "docx_renamed.pdf")

    (FIXTURE_DIR / "malformed.pdf").write_bytes(b"this is not a pdf at all\x00\x01")

    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8))
        pix.clear_with(90)
        page.insert_image(pymupdf.Rect(50, 50, 550, 700), stream=pix.tobytes("png"))
    doc.save(str(FIXTURE_DIR / "scan_imageonly.pdf"))
    doc.close()

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Locked content", fontsize=11, fontname="helv")
    doc.save(
        str(FIXTURE_DIR / "encrypted.pdf"),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        owner_pw="secret",
    )
    doc.close()

    # Legacy Word: OLE compound-file magic + filler — enough for sniffing.
    (FIXTURE_DIR / "legacy.doc").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 120)
    print(f"fixtures rebuilt in {FIXTURE_DIR}")


if __name__ == "__main__":
    build()
