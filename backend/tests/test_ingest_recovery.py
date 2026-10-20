"""F1 exact-source recovery controls; these are not manuscript accuracy labels."""

import json
import sys
from contextlib import suppress
from pathlib import Path

import psutil
import pymupdf
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.ingest import recovery
from app.ingest.recovery_mapping import (
    MappingUnavailable,
    map_layout_reply,
    native_fingerprint,
)
from app.ingest.recovery_models import (
    LayoutBox,
    LayoutCaption,
    LayoutCell,
    LayoutPage,
    LayoutReply,
    LayoutTable,
    RecoveryEnvelope,
)
from app.ingest.schemas import (
    ExtractionResult,
    PageGeometry,
    PageReadingOrder,
    SectionTree,
    SourceLineSpan,
    TextBlock,
)


def source_document(lines=None):
    lines = lines or [
        ("First", (20, 20, 50, 30)),
        ("0", (100, 20, 108, 30)),
        ("Second", (20, 50, 60, 60)),
        ("0", (100, 50, 108, 60)),
    ]
    blocks = [
        TextBlock(
            page=1,
            text=text,
            bbox=box,
            max_font_size=10,
            bold_ratio=0,
            source_line_ids=[f"1:{index}:0"],
            source_lines=[
                SourceLineSpan(
                    start=0,
                    end=len(text),
                    bbox=box,
                    max_font_size=10,
                    bold_ratio=0,
                    source_id=f"1:{index}:0",
                )
            ],
        )
        for index, (text, box) in enumerate(lines)
    ]
    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(text) for text, _ in lines),
        section_tree=SectionTree(source="none"),
        blocks=blocks,
        images=[],
        geometry=[PageGeometry(page=1, width=200, height=200)],
        reading_order=[
            PageReadingOrder(
                page=1,
                status="unresolved",
                reason="table_regions_unresolved",
                source_line_count=len(blocks),
                source_inventory_sha256="frozen-native-inventory",
                ordered_source_line_ids=[block.source_line_ids[0] for block in blocks],
            )
        ],
    )


def box(rect, origin="TOPLEFT"):
    left, top, right, bottom = rect
    if origin == "BOTTOMLEFT":
        top, bottom = 200 - top, 200 - bottom
    return LayoutBox(l=left, t=top, r=right, b=bottom, coord_origin=origin)


def table_reply(extraction):
    table = LayoutTable(
        region_id="table-1",
        bbox=box((15, 15, 120, 65), "BOTTOMLEFT"),
        rows=2,
        cols=2,
        cells=[
            LayoutCell(
                text=block.text,
                bbox=box(block.bbox),
                row_start=index // 2,
                row_end=index // 2 + 1,
                col_start=index % 2,
                col_end=index % 2 + 1,
            )
            for index, block in enumerate(extraction.blocks[:4])
        ],
    )
    return LayoutReply(
        source_sha256="source",
        request_sha256="request",
        manifest_sha256="models",
        versions={"docling": "test"},
        pages=[LayoutPage(page=1, width=200, height=200, status="complete", tables=[table])],
    )


def envelope(extraction):
    return RecoveryEnvelope(
        source_sha256="source",
        native_sha256=native_fingerprint(extraction),
        request_sha256="request",
        manifest_sha256="models",
        requested_pages=[1],
        pages=[],
    )


def mapped(extraction, reply=None, **settings):
    return map_layout_reply(
        extraction,
        envelope(extraction),
        reply or table_reply(extraction),
        Settings(_env_file=None, **settings),
    )


def test_source_lines_and_order_ledger_unchanged_and_roundtrip():
    doc = source_document()
    before = doc.model_dump()
    result = mapped(doc)
    assert result.pages[0].tables[0].status == "source_mapped"
    assert [cell.sources[0].source_id for cell in result.pages[0].tables[0].cells] == [
        "1:0:0",
        "1:1:0",
        "1:2:0",
        "1:3:0",
    ]
    assert doc.model_dump() == before
    doc.recovery = result
    loaded = ExtractionResult.model_validate_json(doc.model_dump_json())
    assert loaded == doc
    assert loaded.reading_order[0].status == "unresolved"
    assert loaded.recovery.pages[0].tables[0].analytical_status == "not_assessed"


def test_repeated_values_need_independent_row_anchors():
    doc = source_document()
    reply = table_reply(doc)
    for index in (1, 3):
        reply.pages[0].tables[0].cells[index].bbox = box((99, 15, 110, 65))
    table = mapped(doc, reply).pages[0].tables[0]
    assert table.status == "source_mapped"
    assert table.cells[1].sources[0].source_id == "1:1:0"
    assert table.cells[3].sources[0].source_id == "1:3:0"
    assert table.cells[1].sources[0].bbox == doc.blocks[1].bbox
    assert table.cells[1].sources[0].bbox != table.cells[1].proposed_bbox


def test_ambiguous_repeated_values_without_row_anchor_stay_unresolved():
    doc = source_document()
    reply = table_reply(doc)
    for cell in reply.pages[0].tables[0].cells:
        cell.text = "0"
        cell.bbox = box((99, 15, 110, 65))
    table = mapped(doc, reply).pages[0].tables[0]
    assert table.status == "partial"
    assert all(not cell.sources for cell in table.cells)


@pytest.mark.parametrize("field", ["source_sha256", "request_sha256", "manifest_sha256"])
def test_wrong_identity_rejected(field):
    doc = source_document()
    reply = table_reply(doc)
    setattr(reply, field, "different")
    with pytest.raises(MappingUnavailable, match="identity_mismatch"):
        mapped(doc, reply)


def test_changed_native_text_cannot_reuse_mapping():
    doc = source_document()
    previous = envelope(doc)
    reply = table_reply(doc)
    doc.blocks[0].text = "Other"
    with pytest.raises(MappingUnavailable, match="identity_mismatch"):
        map_layout_reply(doc, previous, reply, Settings(_env_file=None))


def test_wrong_page_rejected():
    doc = source_document()
    reply = table_reply(doc)
    reply.pages[0].page = 2
    with pytest.raises(MappingUnavailable, match="page_scope"):
        mapped(doc, reply)


@pytest.mark.parametrize("change", ["size", "rotation", "nan", "outside"])
def test_unproven_geometry_not_used(change):
    doc = source_document()
    reply = table_reply(doc)
    if change == "size":
        reply.pages[0].height = 400
    elif change == "rotation":
        doc.geometry[0].rotation = 90
    elif change == "nan":
        with pytest.raises(ValidationError):
            box((0, 0, float("nan"), 10))
        return
    else:
        reply.pages[0].tables[0].bbox = box((15, 15, 250, 65))
    assert mapped(doc, reply).pages[0].status == "unavailable"


@pytest.mark.parametrize("axis", ["row", "col"])
def test_swapped_grid_with_correct_source_text_is_partial(axis):
    doc = source_document()
    reply = table_reply(doc)
    for cell in reply.pages[0].tables[0].cells:
        start = getattr(cell, f"{axis}_start")
        setattr(cell, f"{axis}_start", 1 - start)
        setattr(cell, f"{axis}_end", 2 - start)
    table = mapped(doc, reply).pages[0].tables[0]
    assert table.status == "partial"
    assert f"{'column' if axis == 'col' else 'row'}_geometry_inconsistent" in table.reasons


def test_duplicate_source_cannot_fill_two_cells():
    doc = source_document()
    reply = table_reply(doc)
    reply.pages[0].tables[0].cells[1].text = "First"
    reply.pages[0].tables[0].cells[1].bbox = box(doc.blocks[0].bbox)
    table = mapped(doc, reply).pages[0].tables[0]
    assert "source_occurrence_reused" in table.reasons
    assert not table.cells[0].sources
    assert not table.cells[1].sources


def test_missing_minus_sign_does_not_match():
    doc = source_document()
    doc.blocks[1].text = "-0"
    doc.blocks[1].source_lines[0].end = 2
    reply = table_reply(doc)
    reply.pages[0].tables[0].cells[1].text = "0"
    assert mapped(doc, reply).pages[0].tables[0].cells[1].status == "unresolved"


def test_unassigned_footnote_is_visible_and_prevents_complete_mapping():
    doc = source_document()
    extra = source_document([("Footnote", (20, 62, 60, 68))]).blocks[0]
    extra.source_line_ids = ["1:4:0"]
    extra.source_lines[0].source_id = "1:4:0"
    doc.blocks.append(extra)
    doc.reading_order[0].source_line_count += 1
    doc.reading_order[0].ordered_source_line_ids.append("1:4:0")
    table = mapped(doc).pages[0].tables[0]
    assert table.status == "partial"
    assert table.unmapped_source_ids == ["1:4:0"]


@pytest.mark.parametrize(
    "damage", ["deleted_block", "missing_id", "wrong_count", "no_ledger", "missing_inventory"]
)
def test_damaged_source_ledger_cannot_support_mapping(damage):
    doc = source_document()
    reply = table_reply(doc)
    if damage == "deleted_block":
        doc.blocks.pop()
    elif damage == "missing_id":
        doc.reading_order[0].ordered_source_line_ids.pop()
    elif damage == "wrong_count":
        doc.reading_order[0].source_line_count = 99
    elif damage == "missing_inventory":
        doc.reading_order[0].source_inventory_sha256 = None
    else:
        doc.reading_order = None
    with pytest.raises(MappingUnavailable, match="native_ledger_unavailable"):
        mapped(doc, reply)


def test_shifted_value_with_blank_column_cannot_change_its_header():
    doc = source_document(
        [
            ("A", (20, 20, 30, 30)),
            ("B", (80, 20, 90, 30)),
            ("C", (140, 20, 150, 30)),
            ("10", (20, 50, 35, 60)),
            ("20", (80, 50, 95, 60)),
        ]
    )
    reply = table_reply(doc)
    proposal = reply.pages[0].tables[0]
    proposal.cols = 3
    proposal.bbox = box((15, 15, 160, 65))
    proposal.cells = [
        LayoutCell(
            text=block.text,
            bbox=box(block.bbox),
            row_start=0 if index < 3 else 1,
            row_end=1 if index < 3 else 2,
            col_start=col,
            col_end=col + 1,
        )
        for index, (block, col) in enumerate(zip(doc.blocks, [0, 1, 2, 0, 2], strict=True))
    ]
    proposal.cells.append(
        LayoutCell(text="", bbox=None, row_start=1, row_end=2, col_start=1, col_end=2)
    )
    table = mapped(doc, reply).pages[0].tables[0]
    assert table.status == "partial"
    assert "column_geometry_inconsistent" in table.reasons
    # The same source with the value under B is a retained positive control.
    proposal.cells[4].col_start, proposal.cells[4].col_end = 1, 2
    proposal.cells[5].col_start, proposal.cells[5].col_end = 2, 3
    assert mapped(doc, reply).pages[0].tables[0].status == "source_mapped"


def test_partly_intersecting_source_is_not_silently_outside_table():
    doc = source_document(
        [
            ("First", (20, 20, 50, 30)),
            ("0", (100, 20, 108, 30)),
            ("Second", (20, 50, 60, 60)),
            ("0", (100, 50, 108, 60)),
            ("Footnote", (10, 62, 60, 68)),
        ]
    )
    table = mapped(doc).pages[0].tables[0]
    assert table.status == "partial"
    assert table.unmapped_source_ids == ["1:4:0"]
    assert "table_boundary_ambiguous" in table.reasons


def test_exact_caption_overlap_is_accounted_for_without_hiding_other_content():
    doc = source_document(
        [
            ("First", (20, 20, 50, 30)),
            ("0", (100, 20, 108, 30)),
            ("Second", (20, 50, 60, 60)),
            ("0", (100, 50, 108, 60)),
            ("Caption", (10, 5, 60, 16)),
        ]
    )
    reply = table_reply(doc)
    proposal = reply.pages[0].tables[0]
    proposal.captions = [LayoutCaption(text="Caption", page=1, bbox=box((10, 5, 60, 16)))]
    table = mapped(doc, reply).pages[0].tables[0]
    assert table.status == "source_mapped"
    assert table.unmapped_source_ids == []
    assert table.captions[0].sources[0].source_id == "1:4:0"
    # A claimed caption that is not source-supported cannot explain overlap.
    proposal.captions[0].text = "Invented caption"
    table = mapped(doc, reply).pages[0].tables[0]
    assert table.status == "partial"
    assert "table_boundary_ambiguous" in table.reasons
    assert table.unmapped_source_ids == ["1:4:0"]


def test_wrong_caption_does_not_gain_a_source_box():
    doc = source_document()
    reply = table_reply(doc)
    reply.pages[0].tables[0].captions = [
        LayoutCaption(text="Not in this source", page=1, bbox=box((15, 5, 120, 15)))
    ]
    table = mapped(doc, reply).pages[0].tables[0]
    assert table.status == "partial"
    assert not table.captions[0].sources


@pytest.mark.parametrize(
    ("setting", "value", "reason"),
    [
        ("ingest_recovery_max_cells_per_table", 3, "table_cell_limit"),
        ("ingest_recovery_max_mapping_comparisons", 1, "mapping_budget_exhausted"),
    ],
)
def test_mapping_limits_fail_closed(setting, value, reason):
    result = mapped(source_document(), **{setting: value})
    assert result.pages[0].status == "unavailable"
    assert result.pages[0].reason == reason


def test_overlapping_cell_spans_rejected():
    doc = source_document()
    reply = table_reply(doc)
    reply.pages[0].tables[0].cells[0].col_end = 2
    assert mapped(doc, reply).pages[0].reason == "overlapping_cell_spans"


def test_legacy_store_and_disabled_worker_do_not_start_process(monkeypatch, tmp_path):
    doc = source_document()
    doc.reading_order = None
    monkeypatch.setattr(recovery, "_run_worker", lambda *args: pytest.fail("worker called"))
    assert recovery.recover_layout(doc, tmp_path / "missing.pdf", Settings(_env_file=None)) is doc
    assert doc.recovery is None
    doc = source_document()
    recovery.recover_layout(doc, tmp_path / "missing.pdf", Settings(_env_file=None))
    assert doc.recovery is None


def test_blank_worker_configuration_means_disabled():
    settings = Settings(
        _env_file=None,
        ingest_recovery_python="",
        ingest_recovery_models_dir="",
        ingest_recovery_manifest_file="",
    )
    assert settings.ingest_recovery_python is None
    assert settings.ingest_recovery_models_dir is None
    assert settings.ingest_recovery_manifest_file is None


def test_cached_mapping_is_rederived_from_native_source(monkeypatch, tmp_path):
    path = tmp_path / "input.pdf"
    write_pdf(path)
    doc = source_document()
    calls = []

    def worker(job, *args):
        calls.append(job)
        reply = table_reply(doc)
        reply.source_sha256 = job["source_sha256"]
        reply.request_sha256 = job["request_sha256"]
        reply.manifest_sha256 = job["manifest_sha256"]
        reply.versions = json.loads(Path(job["manifest"]).read_bytes())["packages"]
        return reply, None, 100, 0.1

    monkeypatch.setattr(recovery, "_run_worker", worker)
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        ingest_recovery_python=Path(sys.executable),
        ingest_recovery_models_dir=tmp_path,
        ingest_recovery_manifest_file="",
    )
    recovery.recover_layout(doc, path, settings)
    assert len(calls) == 1
    original = doc.recovery.model_copy(deep=True)
    corrupted = doc.recovery.pages[0].tables[0].cells[0].sources[0]
    corrupted.page = 99
    corrupted.block_index = 999
    corrupted.source_id = "missing-source"
    corrupted.text = "Invented text"
    corrupted.bbox = (150, 150, 180, 180)
    doc = ExtractionResult.model_validate_json(doc.model_dump_json())
    recovery.recover_layout(doc, path, settings)
    assert len(calls) == 1
    assert doc.recovery.pages == original.pages


@pytest.mark.parametrize("complete", [True, False])
def test_ingest_cache_reuses_only_identical_source_and_recipe(monkeypatch, tmp_path, complete):
    from app.ingest import service

    source = tmp_path / "input.pdf"
    write_pdf(source)
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        ingest_recovery_python=Path(sys.executable),
        ingest_recovery_models_dir=tmp_path,
    )
    calls = []

    def worker(job, *args):
        calls.append(job)
        reply = table_reply(source_document())
        reply.source_sha256 = job["source_sha256"]
        reply.request_sha256 = job["request_sha256"]
        reply.manifest_sha256 = job["manifest_sha256"]
        reply.versions = json.loads(Path(job["manifest"]).read_bytes())["packages"]
        if not complete:
            reply.pages[0].status = "unavailable"
        return reply, None, 100, 0.1

    monkeypatch.setattr(recovery, "_run_worker", worker)
    result = service._recover_with_cache(source_document(), source, settings, 1)
    service._write_raw_store(result, service.raw_store_path(settings, 1))
    loaded = service.load_raw_store(settings, 1)
    assert loaded.recovery == result.recovery
    again = service._recover_with_cache(source_document(), source, settings, 1)
    assert len(calls) == 1
    assert again.recovery == result.recovery
    assert again.recovery.pages[0].status == ("processed" if complete else "unavailable")
    # Native text, original bytes and policy changes each invalidate reuse.
    changed = source_document()
    changed.blocks[0].text = "Other"
    service._recover_with_cache(changed, source, settings, 1)
    assert len(calls) == 2
    with source.open("ab") as out:
        out.write(b"\n")
    service._recover_with_cache(source_document(), source, settings, 1)
    assert len(calls) == 3
    settings.ingest_recovery_max_mapping_comparisons += 1
    service._recover_with_cache(source_document(), source, settings, 1)
    assert len(calls) == 4


@pytest.mark.parametrize("failure", ["missing", "damaged", "storage_unavailable"])
def test_recovery_cache_failure_preserves_fresh_native_result(monkeypatch, tmp_path, failure):
    from botocore.exceptions import EndpointConnectionError

    from app.ingest import service

    errors = {
        "missing": FileNotFoundError(),
        "damaged": ValueError(),
        "storage_unavailable": EndpointConnectionError(endpoint_url="https://example.invalid"),
    }

    def unavailable(*args):
        raise errors[failure]

    monkeypatch.setattr(service, "load_raw_store", unavailable)
    observed = []

    def recover(doc, path, settings, previous):
        observed.append(previous)
        return doc

    monkeypatch.setattr(service, "recover_layout", recover)
    doc = source_document()
    result = service._recover_with_cache(
        doc,
        tmp_path / "source.pdf",
        Settings(_env_file=None, ingest_recovery_python=Path(sys.executable)),
        1,
    )
    assert result is doc
    assert observed == [None]


def test_worker_env_does_not_inherit_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-cross")
    monkeypatch.setenv("DATABASE_URL", "must-not-cross")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-cross")
    monkeypatch.setenv("HTTPS_PROXY", "must-not-cross")
    monkeypatch.setenv("PYTHONPATH", "must-not-cross")
    env = recovery.worker_environment(tmp_path, 2)
    assert all(value != "must-not-cross" for value in env.values())
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"


def write_pdf(path, *, cropped=False):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=200, height=200)
        page.insert_text((20, 20), "Source")
        if cropped:
            page.set_cropbox(pymupdf.Rect(10, 10, 190, 190))
        pdf.save(path)


def test_invalid_configuration_preserves_native_content(tmp_path):
    path = tmp_path / "input.pdf"
    write_pdf(path)
    doc = source_document()
    before = doc.model_dump(exclude={"recovery"})
    settings = Settings(
        _env_file=None,
        ingest_recovery_python=Path(sys.executable),
        ingest_recovery_manifest_file=tmp_path / "absent.json",
    )
    result = recovery.recover_layout(doc, path, settings)
    assert result.model_dump(exclude={"recovery"}) == before
    assert result.recovery.pages[0].reason == "recovery_configuration_unavailable"


def test_crop_transform_is_not_assumed(monkeypatch, tmp_path):
    path = tmp_path / "input.pdf"
    write_pdf(path, cropped=True)
    doc = source_document()
    monkeypatch.setattr(recovery, "_run_worker", lambda *args: pytest.fail("worker called"))
    settings = Settings(
        _env_file=None,
        ingest_recovery_python=Path(sys.executable),
        ingest_recovery_models_dir=tmp_path,
    )
    result = recovery.recover_layout(doc, path, settings)
    assert result.recovery.pages[0].reason == "unsupported_page_transform"


def test_cleanup_timeout_preserves_native_work(monkeypatch, tmp_path):
    path = tmp_path / "input.pdf"
    write_pdf(path)
    doc = source_document()
    before = doc.model_dump(exclude={"recovery"})

    def unavailable(*args):
        raise TimeoutError("Worker launcher did not finish terminating")

    monkeypatch.setattr(recovery, "_run_worker", unavailable)
    result = recovery.recover_layout(
        doc,
        path,
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            ingest_recovery_python=Path(sys.executable),
            ingest_recovery_models_dir=tmp_path,
        ),
    )
    assert result.model_dump(exclude={"recovery"}) == before
    assert result.recovery.pages[0].reason == "recovery_unavailable"


@pytest.mark.parametrize("limit", ["time", "memory", "output"])
def test_real_subprocess_limits(monkeypatch, tmp_path, limit):
    script = tmp_path / "recovery_worker.py"
    script.write_text(
        "import pathlib, sys, time\n"
        "target = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])\n"
        + ("target.write_text('x' * 2000)\n" if limit == "output" else "")
        + "time.sleep(10)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(recovery, "__file__", str(tmp_path / "recovery.py"))
    settings = Settings(
        _env_file=None,
        ingest_recovery_python=Path(sys.executable),
        ingest_recovery_timeout_seconds=0.2 if limit == "time" else 3,
        ingest_recovery_memory_mb=1 if limit == "memory" else 100,
        ingest_recovery_poll_seconds=0.01,
    )
    reply, reason, _, elapsed = recovery._run_worker({"max_output_bytes": 1000}, tmp_path, settings)
    assert reply is None
    assert reason == f"worker_{limit}_limit"
    assert elapsed < 5


def test_malformed_worker_output_is_not_success(monkeypatch, tmp_path):
    (tmp_path / "recovery_worker.py").write_text(
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output')+1]).write_text('{')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(recovery, "__file__", str(tmp_path / "recovery.py"))
    result, reason, _, _ = recovery._run_worker(
        {"max_output_bytes": 1000},
        tmp_path,
        Settings(_env_file=None, ingest_recovery_python=Path(sys.executable)),
    )
    assert result is None
    assert reason == "worker_output_invalid"


def test_fast_worker_exit_does_not_leave_unobserved_descendants(monkeypatch, tmp_path):
    sentinel = tmp_path / "helper.pid"
    helper = (
        "import os, pathlib, time; "
        f"pathlib.Path({str(sentinel)!r}).write_text(str(os.getpid())); time.sleep(10)"
    )
    (tmp_path / "recovery_worker.py").write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-I', '-c', {helper!r}])\n"
        "time.sleep(0.15)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(recovery, "__file__", str(tmp_path / "recovery.py"))
    settings = Settings(
        _env_file=None,
        ingest_recovery_python=Path(sys.executable),
        ingest_recovery_poll_seconds=0.5,
    )
    child = None
    try:
        reply, reason, _, _ = recovery._run_worker({"max_output_bytes": 1000}, tmp_path, settings)
        assert reply is None
        assert reason == "worker_unavailable"
        assert sentinel.is_file(), "The helper must actually start before its parent exits"
        pid = int(sentinel.read_text())
        try:
            child = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
    finally:
        # The failing-before-fix test must not itself leak a helper.
        if child is not None:
            with suppress(psutil.NoSuchProcess):
                descendants = child.children(recursive=True)
                for process in [*descendants, child]:
                    with suppress(psutil.NoSuchProcess):
                        process.kill()
                psutil.wait_procs([*descendants, child], timeout=2)


def test_worker_projection_preserves_each_coordinate_origin():
    from app.ingest.recovery_worker import project_document

    doc = {
        "pages": {"3": {"page_no": 3, "size": {"width": 200, "height": 200}}},
        "tables": [
            {
                "self_ref": "table",
                "prov": [{"page_no": 3, "bbox": box((15, 15, 120, 65), "BOTTOMLEFT").model_dump()}],
                "data": {
                    "num_rows": 1,
                    "num_cols": 1,
                    "table_cells": [
                        {
                            "text": "Value",
                            "bbox": box((20, 20, 40, 30)).model_dump(),
                            "start_row_offset_idx": 0,
                            "end_row_offset_idx": 1,
                            "start_col_offset_idx": 0,
                            "end_col_offset_idx": 1,
                        }
                    ],
                },
            }
        ],
    }
    result = LayoutPage.model_validate(project_document(doc, 3))
    assert result.tables[0].bbox.coord_origin == "BOTTOMLEFT"
    assert result.tables[0].cells[0].bbox.coord_origin == "TOPLEFT"
