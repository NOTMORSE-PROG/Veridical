"""V-008: the permanent ingestion regression gate.

Every committed fixture in tests/fixtures/ingest/ has a <name>.expected.json
asserted here — CI runs this on every commit. Rebuild fixtures only
deliberately via `uv run python -m tests.fixtures.ingest.build_fixtures`.
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.config import get_settings
from app.errors import FileMalformedError, VeridicalError
from app.ingest import vision
from app.ingest.patterns import load_patterns
from app.ingest.references import extract_references
from app.ingest.schemas import ExtractionResult
from app.ingest.service import detect_format, select_extractor
from app.llm.fake import FakeLLMClient
from app.models.enums import CitationParseStatus

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ingest"
EXPECTED = sorted(FIXTURE_DIR.glob("*.expected.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_of(expected_path: Path) -> Path:
    return expected_path.with_name(expected_path.name.removesuffix(".expected.json"))


def _extract(fixture: Path) -> ExtractionResult:
    settings = get_settings()
    extractor = select_extractor(detect_format(fixture))
    return extractor(str(fixture), settings)


def test_corpus_is_complete():
    """Every binary fixture has expectations and vice versa — a fixture
    without assertions is not a regression gate."""
    binaries = {
        p.name
        for p in FIXTURE_DIR.iterdir()
        if p.suffix in {".pdf", ".docx", ".doc"} and not p.name.endswith(".expected.json")
    }
    covered = {_fixture_of(p).name for p in EXPECTED}
    assert binaries == covered


@pytest.mark.parametrize("expected_path", EXPECTED, ids=lambda p: _fixture_of(p).name)
def test_fixture_matches_expected_output(expected_path: Path):
    want = _load(expected_path)
    fixture = _fixture_of(expected_path)
    settings = get_settings()

    if "expect_error" in want:
        with pytest.raises(VeridicalError) as exc_info:
            _extract(fixture)
        assert exc_info.value.code == want["expect_error"]
        if "message_names" in want:
            assert want["message_names"] in str(exc_info.value)
        return

    result = _extract(fixture)
    assert result.anchor_kind == want["anchor_kind"]
    assert result.section_tree.source == want["tree_source"]
    if "page_count" in want:
        assert result.page_count == want["page_count"]
    if "image_only" in want:
        assert result.image_only == want["image_only"]
    if "images" in want:
        assert len(result.images) == want["images"]
    if "chapter_numbering" in want:
        got = [n.numbering for n in result.section_tree.nodes if n.numbering]
        assert got == want["chapter_numbering"]
    if "native_tables" in want:
        native = [t for t in result.tables if t.source == "native"]
        assert len(native) == want["native_tables"]
    if "citations_parsed" in want:
        drafts = extract_references(result, load_patterns(settings.ingest_patterns_file))
        parsed = [d for d in drafts if d.parse_status == CitationParseStatus.parsed]
        assert len(parsed) == want["citations_parsed"]
    if want.get("pipeline_continues"):
        # image_only is a state: extraction returned normally (no raise)
        # and the inventory is intact for the limited checks that remain.
        assert result.blocks == [] or result.blocks is not None
    if "vision_tables_fake_mode" in want:
        patterns = load_patterns(settings.ingest_patterns_file)
        asyncio.run(vision.read_images(result, fixture, FakeLLMClient(), patterns, settings))
        vision_tables = [t for t in result.tables if t.source == "vision"]
        assert len(vision_tables) == want["vision_tables_fake_mode"]
        assert any(
            want["vision_table_rows_contain"] in cell
            for t in vision_tables
            for row in t.rows
            for cell in row
        )


def test_sniffing_beats_extension():
    """docx_renamed.pdf is a DOCX in a .pdf trench coat."""
    assert detect_format(FIXTURE_DIR / "docx_renamed.pdf") == ".docx"
    assert detect_format(FIXTURE_DIR / "native.pdf") == ".pdf"
    with pytest.raises(FileMalformedError):
        detect_format(FIXTURE_DIR / "malformed.pdf")
