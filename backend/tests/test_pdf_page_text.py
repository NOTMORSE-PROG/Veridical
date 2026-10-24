"""F8/4w: source-bound reading aid; corrupted caches never become evidence."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from app.config import Settings
from app.errors import ConflictError, GoneError, RateLimitedError
from app.ingest.patterns import load_patterns
from app.ingest.pdf import _extract_open_document
from app.ingest.service import _write_raw_store, raw_store_path
from app.models.enums import IngestStatus
from app.report.page_text import manuscript_page_text_for, page_text_admission


@pytest.fixture
def source(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, storage_backend="local")
    path = tmp_path / "misnamed.docx"
    with pymupdf.open() as document:
        for number in range(1, 4):
            page = document.new_page()
            page.insert_text((80, 120), f"Unique source page {number}.", fontsize=10)
            page.insert_text((80, 160), "Another independent complete block.", fontsize=10)
        path.write_bytes(document.tobytes())
        extraction = _extract_open_document(document, load_patterns(), settings)
    assert all(record.status == "ordered" for record in extraction.reading_order)
    manuscript = SimpleNamespace(
        id=1,
        file_ref=str(path),
        purged_at=None,
        ingest_status=IngestStatus.done,
        content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    _write_raw_store(extraction, raw_store_path(settings, manuscript.id))
    return settings, manuscript, extraction


def read(source, page=2):
    settings, manuscript, _ = source
    return manuscript_page_text_for(manuscript, 7, page, settings)


def save(source):
    settings, manuscript, extraction = source
    _write_raw_store(extraction, raw_store_path(settings, manuscript.id))


def test_exact_page_and_original_names_do_not_change_identity(source):
    settings, manuscript, _ = source
    original = Path(manuscript.file_ref)
    for name in ["original.pdf", "wrong.txt", "wrong.docx", "nosuffix"]:
        target = settings.data_dir / name
        target.write_bytes(original.read_bytes())
        manuscript.file_ref = str(target)
        result = read(source)
        assert result.state == "available" and result.page_count == 3
        assert (result.page, result.manuscript_id, result.check_run_id) == (2, 1, 7)
        assert result.saved_block_count == 2 and result.omitted_block_count == 0
        assert "Unique source page 2." in result.model_dump_json()
        assert "Unique source page 1." not in result.model_dump_json()
        assert "Unique source page 3." not in result.model_dump_json()


@pytest.mark.parametrize(
    "damage",
    [
        "text",
        "offset",
        "geometry",
        "font",
        "bold",
        "missing_line",
        "duplicate_line",
        "reorder_blocks",
        "joint_permutation",
        "inventory",
        "line_count",
        "duplicate_order",
        "page_count",
        "anchor_kind",
    ],
)
def test_corrupt_saved_witness_never_returns_text(source, damage):
    _, _, extraction = source
    blocks = [block for block in extraction.blocks if block.page == 2]
    record = extraction.reading_order[1]
    if damage == "text":
        blocks[0].text = "Invented source evidence"
    elif damage == "offset":
        blocks[0].source_lines[0].start = 1
    elif damage == "geometry":
        blocks[0].source_lines[0].bbox = (0, 0, 10, 10)
    elif damage == "font":
        blocks[0].source_lines[0].max_font_size += 1
    elif damage == "bold":
        blocks[0].source_lines[0].bold_ratio = 1
    elif damage == "missing_line":
        extraction.blocks.remove(blocks[0])
    elif damage == "duplicate_line":
        extraction.blocks.append(blocks[0].model_copy(deep=True))
    elif damage in {"reorder_blocks", "joint_permutation"}:
        left = extraction.blocks.index(blocks[0])
        right = extraction.blocks.index(blocks[1])
        extraction.blocks[left], extraction.blocks[right] = blocks[1], blocks[0]
        if damage == "joint_permutation":
            record.ordered_source_line_ids.reverse()
    elif damage == "inventory":
        record.source_inventory_sha256 = "0" * 64
    elif damage == "line_count":
        record.source_line_count += 1
    elif damage == "duplicate_order":
        extraction.reading_order.append(record.model_copy(deep=True))
    elif damage == "page_count":
        extraction.page_count += 1
    elif damage == "anchor_kind":
        extraction.anchor_kind = "paragraph"
    save(source)
    result = read(source)
    assert result.state == "unavailable" and result.blocks == []
    assert result.reason == "source_binding_unverified"


@pytest.mark.parametrize(
    "state", ["legacy", "unresolved", "missing_extraction", "hash_mismatch", "no_hash"]
)
def test_unknown_and_unresolved_are_distinct_from_empty(source, state):
    settings, manuscript, extraction = source
    if state == "legacy":
        extraction.reading_order = None
    elif state == "unresolved":
        extraction.reading_order[1].status = "unresolved"
    elif state == "hash_mismatch":
        manuscript.content_hash = "0" * 64
    elif state == "no_hash":
        manuscript.content_hash = None
    save(source)
    if state == "missing_extraction":
        raw_store_path(settings, manuscript.id).unlink()
    result = read(source)
    assert result.state == "unavailable" and not result.blocks
    assert (
        result.reason
        == {
            "legacy": "reading_order_not_recorded",
            "unresolved": "reading_order_unresolved",
            "missing_extraction": "saved_text_unavailable",
            "hash_mismatch": "source_binding_unverified",
            "no_hash": "source_binding_unverified",
        }[state]
    )


def test_missing_local_original_does_not_trigger_unbounded_download(source, monkeypatch):
    _, manuscript, _ = source
    Path(manuscript.file_ref).unlink()
    import app.storage

    monkeypatch.setattr(
        app.storage, "get_storage", lambda *_: pytest.fail("cold storage allocation")
    )
    assert read(source).reason == "source_cache_unavailable"


def test_purge_forbids_retained_text(source):
    from datetime import UTC, datetime

    source[1].purged_at = datetime.now(UTC)
    with pytest.raises(GoneError):
        read(source)


@pytest.mark.parametrize("page", [0, -1, 4, 999999])
def test_exact_original_bounds_never_clamp(source, page):
    result = read(source, page)
    assert result.reason == "page_out_of_range" and result.page_count == 3
    assert result.page == page and not result.blocks


@pytest.mark.parametrize("limit", ["blocks", "chars", "bytes", "raw"])
def test_resource_limits_never_clip_a_source_block(source, limit):
    settings, _, _ = source
    if limit == "blocks":
        settings.pdf_page_text_max_blocks = 1
    elif limit == "chars":
        settings.pdf_page_text_max_chars = 1
    elif limit == "bytes":
        settings.pdf_page_text_max_bytes = 256
    else:
        settings.pdf_page_text_raw_max_bytes = 10
    result = read(source)
    if limit == "raw":
        assert result.state == "unavailable" and result.reason == "resource_limit"
    else:
        assert result.state == "available" and result.omitted_block_count > 0
        assert all(block.text.endswith(".") for block in result.blocks)
        assert len(result.model_dump_json().encode()) <= settings.pdf_page_text_max_bytes


def test_nonblocking_admission_and_exception_release(source):
    settings = source[0]
    with (
        page_text_admission(settings),
        pytest.raises(RateLimitedError),
        page_text_admission(settings),
    ):
        pytest.fail("queued instead of rejected")
    with pytest.raises(ValueError), page_text_admission(settings):
        raise ValueError("failed read")
    with page_text_admission(settings):
        pass


def test_unreadable_original_never_uses_retained_saved_text(source):
    _, manuscript, _ = source
    Path(manuscript.file_ref).write_bytes(b"%PDF-1.4 broken")
    manuscript.content_hash = hashlib.sha256(Path(manuscript.file_ref).read_bytes()).hexdigest()
    with pytest.raises(ConflictError):
        read(source)


@pytest.mark.parametrize("filename", ["native.pdf", "image_table.pdf", "scan_imageonly.pdf"])
def test_real_committed_fixture_source_policy_and_empty_inventory(source, filename):
    from tests.test_ingest_fixtures import FIXTURE_DIR

    settings, manuscript, _ = source
    payload = (FIXTURE_DIR / filename).read_bytes()
    Path(manuscript.file_ref).write_bytes(payload)
    manuscript.content_hash = hashlib.sha256(payload).hexdigest()
    with pymupdf.open(manuscript.file_ref) as document:
        extraction = _extract_open_document(document, load_patterns(), settings)
    source = settings, manuscript, extraction
    save(source)
    for page in range(1, extraction.page_count + 1):
        result = read(source, page)
        if filename == "scan_imageonly.pdf":
            assert result.state == "no_selectable_text"
            assert result.saved_block_count == 0 and result.omitted_block_count == 0
        elif extraction.reading_order[page - 1].status == "ordered":
            assert result.state == "available"
        else:
            assert result.state == "unavailable" and not result.blocks


def test_deleting_saved_tables_cannot_authorize_table_flattening(source):
    settings, manuscript, _ = source
    with pymupdf.open() as document:
        page = document.new_page()
        for x in [60, 170, 280]:
            page.draw_line((x, 80), (x, 140))
        for y in [80, 110, 140]:
            page.draw_line((60, y), (280, y))
        for x, y, text in [
            (65, 100, "Group"),
            (175, 100, "Count"),
            (65, 130, "A"),
            (175, 130, "12"),
        ]:
            page.insert_text((x, y), text, fontsize=10)
        payload = document.tobytes()
        extraction = _extract_open_document(document, load_patterns(), settings)
    Path(manuscript.file_ref).write_bytes(payload)
    manuscript.content_hash = hashlib.sha256(payload).hexdigest()
    assert extraction.tables
    page = extraction.tables[0].page
    extraction.tables = []
    extraction.reading_order[page - 1].status = "ordered"
    source = settings, manuscript, extraction
    save(source)
    result = read(source, page)
    assert result.state == "unavailable" and result.reason == "reading_order_unresolved"
    assert not result.blocks
