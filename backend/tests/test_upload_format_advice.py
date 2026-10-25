"""F1/F2: recognition failures give recovery advice supported by the bytes."""

import base64
import zipfile

import docx
import pytest

from app import messages
from app.config import Settings
from app.errors import FileMalformedError
from app.ingest.service import EXTRACTORS, detect_format, select_extractor


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        (
            "picture.png",
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQ0AAAAASUVORK5CYII="
            ),
        ),
        ("picture.jpg", b"\xff\xd8\xff\xe0JPEG signature"),
        ("picture.pdf", b"\xff\xd8\xff\xe0JPEG signature"),
        ("unknown.docx", b"unrecognized input"),
        ("empty.pdf", b""),
        ("partial.pdf", b"%PD"),
    ],
    ids=["png", "jpeg-signature", "renamed-image", "unknown", "empty", "partial-signature"],
)
def test_unrecognized_content_gives_neutral_supported_format_advice(tmp_path, filename, payload):
    path = tmp_path / filename
    path.write_bytes(payload)
    with pytest.raises(FileMalformedError) as raised:
        detect_format(path, Settings(_env_file=None))
    text = str(raised.value)
    assert raised.value.code == "file_malformed"
    assert text == (
        "VERIDICAL could not recognize this file as a supported document. "
        "Choose a document in one of these formats: .docx, .pdf."
    )
    assert "corrupt" not in text.lower() and "re-export" not in text.lower()


def test_valid_non_word_zip_does_not_imply_a_corrupt_document(tmp_path):
    path = tmp_path / "spreadsheet.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
    with pytest.raises(FileMalformedError) as raised:
        detect_format(path, Settings(_env_file=None))
    assert "could not recognize" in str(raised.value)
    assert "Choose a document" in str(raised.value)
    assert "corrupt" not in str(raised.value)


def test_neutral_advice_uses_the_registered_supported_formats(tmp_path, monkeypatch):
    path = tmp_path / "unknown"
    path.write_bytes(b"unknown")
    monkeypatch.setitem(EXTRACTORS, ".additional", lambda *_: None)
    with pytest.raises(FileMalformedError) as raised:
        detect_format(path, Settings(_env_file=None))
    assert "formats: .additional, .docx, .pdf." in str(raised.value)


@pytest.mark.parametrize("payload", [b"PK\x03\x04truncated", b"%PDF-1.7\ntruncated"])
def test_recognized_unreadable_document_retains_its_recovery(tmp_path, payload):
    path = tmp_path / "broken"
    path.write_bytes(payload)
    settings = Settings(_env_file=None)
    with pytest.raises(FileMalformedError) as raised:
        select_extractor(detect_format(path, settings))(str(path), settings)
    assert str(raised.value) == messages.FILE_UNREADABLE


def test_word_content_is_recognized_despite_a_misleading_suffix(tmp_path):
    path = tmp_path / "required-format.pdf"
    document = docx.Document()
    document.add_paragraph("Synthetic supported document")
    document.save(path)
    assert detect_format(path, Settings(_env_file=None)) == ".docx"


def test_legacy_word_keeps_its_named_unsupported_type_advice(tmp_path):
    path = tmp_path / "legacy.pdf"
    path.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1"))
    assert detect_format(path, Settings(_env_file=None)) == ".doc"
    with pytest.raises(FileMalformedError) as raised:
        select_extractor(".doc")
    assert "Unsupported file type '.doc'" in str(raised.value)
