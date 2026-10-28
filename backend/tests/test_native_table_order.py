"""Native table order must conserve source and refuse unproved layouts (F1)."""

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from app.config import Settings
from app.ingest.patterns import load_patterns
from app.ingest.pdf import _extract_open_document, _native_tables_in_source_space
from app.ingest.quality import reading_order_issues
from app.ingest.schemas import TableBlock
from app.ingest.service import _write_raw_store, raw_store_path
from app.models.enums import IngestStatus
from app.report.page_text import manuscript_page_text_for


def draw_grid(page, *, top=80, row_height=35, values=None, divider=170):
    values = values or [["Group", "Count"], ["Alpha", "12"]]
    bottom = top + row_height * len(values)
    for x in (60, divider, 280):
        page.draw_line((x, top), (x, bottom))
    for index in range(len(values) + 1):
        y = top + index * row_height
        page.draw_line((60, y), (280, y))
    for row, cells in enumerate(values):
        for column, text in enumerate(cells):
            if text:
                page.insert_text(
                    ((65, divider + 5)[column], top + row_height * row + 22), text, fontsize=10
                )


def extract(document, **settings):
    return _extract_open_document(document, load_patterns(), Settings(_env_file=None, **settings))


def line_facts(extraction):
    return {
        line.source_id: (
            block.text[line.start : line.end],
            line.bbox,
            line.max_font_size,
            line.bold_ratio,
            line.is_furniture,
        )
        for block in extraction.blocks
        for line in block.source_lines or []
    }


@pytest.mark.parametrize(
    "values",
    [
        [["Group", "Count"], ["Alpha", "12"]],
        [["Repeated", "Repeated"], ["Repeated", "Repeated"]],
        [["Group", "Count"], ["", "12"]],
        [["Group", "Count"], ["First line\nSecond line", "12"]],
    ],
)
def test_regular_cells_conserve_every_original_line_and_table_fact(values):
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((65, 60), "Prose before the table.", fontsize=10)
        draw_grid(page, values=values, row_height=45)
        page.insert_text((65, 220), "Prose after the table.", fontsize=10)
        old = extract(doc, ingest_native_table_order_enabled=False)
        new = extract(doc)
    assert old.reading_order[0].status == "unresolved"
    assert new.reading_order[0].status == "ordered"
    assert new.reading_order[0].table_order.version == "native_cells_v1"
    assert not reading_order_issues(new)
    assert line_facts(new) == line_facts(old)
    assert new.tables == old.tables and new.images == old.images and new.uri_links == old.uri_links
    ids = new.reading_order[0].ordered_source_line_ids
    assert len(ids) == len(set(ids)) == len(line_facts(old))
    text = " ".join(block.text for block in new.blocks)
    assert text.index("Prose before") < text.index(
        "Group" if values[0][0] == "Group" else "Repeated"
    )
    assert text.index("Prose after") > text.index(values[-1][-1])


def atomic_document(*, interleaved=False, cut_glyph=False):
    doc = pymupdf.open()
    page = doc.new_page()
    prefix, suffix = "Development ", "Developer Laptop"
    divider = 65 + pymupdf.get_text_length(prefix, fontname="helv", fontsize=10)
    if cut_glyph:
        divider += pymupdf.get_text_length("D", fontname="helv", fontsize=10) / 2
    for x in (60, divider, 300):
        page.draw_line((x, 80), (x, 150))
    for y in (80, 150):
        page.draw_line((60, y), (300, y))
    page.insert_text((65, 105), prefix + suffix, fontsize=10)
    if interleaved:
        page.insert_text((65, 130), "Additional cell text", fontsize=8)
    return doc


def test_complete_contiguous_cell_run_keeps_the_original_line_atomic():
    with atomic_document() as doc:
        old = extract(doc, ingest_native_table_order_enabled=False)
        new = extract(doc)
    record = new.reading_order[0]
    assert record.status == "ordered" and not reading_order_issues(new)
    assert line_facts(old) == line_facts(new)
    cells = record.table_order.tables[0].cells
    assert cells[0].source_line_ids == cells[1].source_line_ids
    assert len(cells[0].source_line_ids) == 1
    runs = [run for cell in cells for run in cell.character_runs]
    assert runs[0].start == 0 and runs[0].end == runs[1].start
    assert runs[1].end == runs[1].raw_character_count
    assert len(record.ordered_source_line_ids) == 1


def test_cached_cross_cell_text_cannot_change_while_its_witness_stays_unchanged():
    with atomic_document() as doc:
        result = extract(doc)
    assert not reading_order_issues(result)
    block = next(block for block in result.blocks if "Development" in block.text)
    block.text = block.text.replace("Development", "Falsifiedxx")
    assert reading_order_issues(result)


@pytest.mark.parametrize("native", [False, True])
def test_supplemental_vision_tables_do_not_change_native_witness_inventory(native):
    with pymupdf.open() as doc:
        page = doc.new_page()
        if native:
            draw_grid(page)
        else:
            page.insert_text((65, 60), "Native prose remains independently ordered.")
        result = extract(doc)
    vision = TableBlock(page=1, rows=[["Supplemental", "7"]], source="vision", low_confidence=True)
    result.tables.append(vision)
    assert not reading_order_issues(result)
    assert result.tables[-1] == vision and result.tables[-1].low_confidence
    if native:
        result.reading_order[0].table_order = None
        assert reading_order_issues(result)


def test_concurrent_native_table_helpers_materialize_each_documents_own_characters(monkeypatch):
    first_found = threading.Event()
    second_attempted = threading.Event()
    first_snapshotted = threading.Event()
    original_find = pymupdf.Page.find_tables
    from app.ingest import pdf

    original_snapshot = pdf.snapshot_native_table
    with pymupdf.open() as first, pymupdf.open() as second:
        first_page, second_page = first.new_page(), second.new_page()
        draw_grid(first_page, values=[["Owner A", "11"], ["Alpha", "12"]])
        draw_grid(second_page, values=[["Private B", "99"], ["Beta", "24"]])

        def find(page, *args, **kwargs):
            if page is second_page:
                assert first_snapshotted.is_set()
            finder = original_find(page, *args, **kwargs)
            if page is first_page:
                first_found.set()
                assert second_attempted.wait(timeout=5)
            return finder

        def snapshot(table, rows, proposal, settings):
            original_snapshot(table, rows, proposal, settings)
            if rows[0][0] == "Owner A":
                first_snapshotted.set()

        def run_second():
            assert first_found.wait(timeout=5)
            second_attempted.set()
            return _native_tables_in_source_space(second_page, 1)

        monkeypatch.setattr(pymupdf.Page, "find_tables", find)
        monkeypatch.setattr(pdf, "snapshot_native_table", snapshot)
        from app.ingest.native_table_order import NativeTableProposal

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(
                _native_tables_in_source_space,
                first_page,
                1,
                ordering=NativeTableProposal(),
                settings=Settings(_env_file=None),
            )
            b = pool.submit(run_second)
            assert a.result(timeout=10)[0].rows[0] == ["Owner A", "11"]
            assert b.result(timeout=10)[0].rows[0] == ["Private B", "99"]


def test_native_table_discovery_failure_restores_page_and_releases_lock(monkeypatch):
    original = pymupdf.Page.find_tables
    with pymupdf.open() as doc:
        page = doc.new_page()
        draw_grid(page)
        page.set_rotation(90)
        crop = tuple(page.cropbox)

        def fail(*args, **kwargs):
            raise RuntimeError("native discovery failed")

        monkeypatch.setattr(pymupdf.Page, "find_tables", fail)
        with pytest.raises(RuntimeError, match="native discovery failed"):
            _native_tables_in_source_space(page, 1)
        assert page.rotation == 90 and tuple(page.cropbox) == crop
        monkeypatch.setattr(pymupdf.Page, "find_tables", original)
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(_native_tables_in_source_space, page, 1).result(timeout=5)


@pytest.mark.parametrize("options", [{"interleaved": True}, {"cut_glyph": True}])
def test_cross_cell_ownership_never_guesses_or_interleaves(options):
    with atomic_document(**options) as doc:
        result = extract(doc)
    assert result.reading_order[0].status == "unresolved"
    assert result.reading_order[0].table_order is None
    assert result.tables and reading_order_issues(result)


def test_two_tables_keep_intermediate_prose_and_independent_identities():
    with pymupdf.open() as doc:
        page = doc.new_page()
        draw_grid(page)
        page.insert_text((65, 185), "Prose between two tables.", fontsize=10)
        draw_grid(page, top=230, values=[["Second table", "Value"], ["Beta", "24"]])
        old = extract(doc, ingest_native_table_order_enabled=False)
        new = extract(doc)
    assert new.reading_order[0].status == "ordered" and not reading_order_issues(new)
    assert len(new.reading_order[0].table_order.tables) == 2
    assert line_facts(new) == line_facts(old)
    text = " ".join(block.text for block in new.blocks)
    assert text.index("12") < text.index("Prose between") < text.index("Second table")


def test_merged_cells_retain_native_data_but_are_outside_the_admitted_grid_family():
    with pymupdf.open() as doc:
        page = doc.new_page()
        for x in (60, 280):
            page.draw_line((x, 80), (x, 150))
        page.draw_line((170, 115), (170, 150))
        for y in (80, 115, 150):
            page.draw_line((60, y), (280, y))
        page.insert_text((65, 100), "Merged header", fontsize=10)
        page.insert_text((65, 140), "Alpha", fontsize=10)
        page.insert_text((175, 140), "12", fontsize=10)
        result = extract(doc)
    assert result.reading_order[0].status == "unresolved"
    assert result.reading_order[0].table_order is None
    assert "Merged header" in result.tables[0].rows[0][0]
    assert reading_order_issues(result)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_table_proposals_restore_native_page_rotation_and_crop(rotation):
    with pymupdf.open() as doc:
        page = doc.new_page()
        draw_grid(page)
        page.set_rotation(rotation)
        original_crop = tuple(page.cropbox)
        result = extract(doc)
        assert page.rotation == rotation and tuple(page.cropbox) == original_crop
    assert result.reading_order[0].status == "ordered"
    assert not reading_order_issues(result)


@pytest.mark.parametrize(
    "setting,value,atomic",
    [
        ("ingest_native_table_max_tables_per_page", 1, False),
        ("ingest_native_table_max_cells_per_page", 1, False),
        ("ingest_native_table_max_characters_per_page", 1, True),
        ("ingest_reading_order_max_pair_comparisons", 1, True),
    ],
)
def test_adapter_budget_failure_retains_source_and_table_data(setting, value, atomic):
    doc = atomic_document() if atomic else pymupdf.open()
    try:
        if not atomic:
            page = doc.new_page()
            draw_grid(page)
            draw_grid(page, top=230)
        baseline = extract(doc, ingest_native_table_order_enabled=False)
        result = extract(doc, **{setting: value})
    finally:
        doc.close()
    assert result.reading_order[0].status == "unresolved"
    assert result.reading_order[0].table_order is None
    assert line_facts(result) == line_facts(baseline)
    assert result.tables == baseline.tables


@pytest.fixture
def stored_atomic_source(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, storage_backend="local")
    path = tmp_path / "atomic.pdf"
    with atomic_document() as doc:
        path.write_bytes(doc.tobytes())
        extraction = extract(doc)
    assert extraction.reading_order[0].status == "ordered"
    manuscript = SimpleNamespace(
        id=1,
        file_ref=str(path),
        purged_at=None,
        ingest_status=IngestStatus.done,
        content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    return settings, manuscript, extraction


def read_stored(source):
    settings, manuscript, extraction = source
    _write_raw_store(extraction, raw_store_path(settings, manuscript.id))
    return manuscript_page_text_for(manuscript, 1, 1, settings)


def test_real_source_read_replays_character_and_cell_proof(stored_atomic_source):
    result = read_stored(stored_atomic_source)
    assert result.state == "available"
    assert "Development Developer Laptop" in " ".join(block.text for block in result.blocks)


@pytest.mark.parametrize(
    "tamper",
    [
        "missing",
        "hash",
        "cell_text",
        "cell_id",
        "range_gap",
        "character_hash",
        "tolerance",
        "table_data",
    ],
)
def test_source_read_rejects_missing_changed_or_forged_table_proof(stored_atomic_source, tamper):
    _, _, extraction = stored_atomic_source
    record = extraction.reading_order[0]
    witness = record.table_order
    cell = witness.tables[0].cells[0]
    if tamper == "missing":
        record.table_order = None
    elif tamper == "hash":
        witness.source_inventory_sha256 = "0" * 64
    elif tamper == "cell_text":
        cell.text_sha256 = "0" * 64
    elif tamper == "cell_id":
        cell.source_line_ids = ["1:900:0"]
    elif tamper == "range_gap":
        cell.character_runs[0].start = 1
    elif tamper == "character_hash":
        for c in witness.tables[0].cells:
            for run in c.character_runs:
                run.raw_character_sha256 = "0" * 64
        # Internal summary recomputation cannot authorize changed original bytes.
        from app.ingest.native_table_order import _digest

        run = cell.character_runs[0]
        witness.character_inventory_sha256 = _digest(
            [(run.source_line_id, run.raw_character_sha256, run.raw_character_count)]
        )
    elif tamper == "tolerance":
        witness.geometry_tolerance_pts *= 2
    elif tamper == "table_data":
        extraction.tables = []
    result = read_stored(stored_atomic_source)
    assert result.state == "unavailable" and not result.blocks
    assert result.reason == "source_binding_unverified"


def test_stored_witness_is_source_bound_not_a_semantic_grade(stored_atomic_source):
    _, manuscript, extraction = stored_atomic_source
    assert not reading_order_issues(extraction)
    assert all(block.text for block in extraction.blocks)
    assert not extraction.figure_readings
    assert Path(manuscript.file_ref).read_bytes()
