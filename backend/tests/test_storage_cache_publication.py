"""Concurrent source readers must never consume a partially published cache file."""

import io
import multiprocessing
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf
import pytest

from app import storage as storage_module
from app.config import Settings
from app.ingest import service as ingestion
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock
from app.models.manuscript import Manuscript
from app.report import service as report
from app.report import source_files
from app.storage import ensure_local_file
from tests.test_ingest_fixtures import FIXTURE_DIR


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def deny_network(*_args, **_kwargs):
        raise AssertionError("Storage publication controls must remain offline")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)


class DurableBytes:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.reads = 0

    def get_bytes(self, _key: str) -> bytes:
        self.reads += 1
        return self.payload


class PartialWrite:
    """Pause after real partial disk I/O, regardless of the publication strategy."""

    def __init__(self, file, written: threading.Event, release: threading.Event) -> None:
        self.file = file
        self.written = written
        self.release = release

    def __getattr__(self, name):
        return getattr(self.file, name)

    def __enter__(self):
        self.file.__enter__()
        return self

    def __exit__(self, *args):
        return self.file.__exit__(*args)

    def write(self, data) -> int:
        first = self.file.write(data[:1])
        self.file.flush()
        self.written.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("Controlled first writer was not released")
        return first + self.file.write(data[1:])


def pause_first_writer(monkeypatch, written, release):
    original_open = io.open

    def controlled_open(*args, **kwargs):
        file = original_open(*args, **kwargs)
        mode = kwargs.get("mode", args[1] if len(args) > 1 else "r")
        if threading.current_thread().name == "cold-fill_0" and "w" in mode and "b" in mode:
            return PartialWrite(file, written, release)
        return file

    monkeypatch.setattr(io, "open", controlled_open)


@pytest.mark.parametrize("source_kind", ["pdf", "extraction"])
def test_second_actual_source_reader_cannot_consume_first_partial_write(
    tmp_path, monkeypatch, source_kind
):
    settings = Settings(_env_file=None, data_dir=tmp_path, veridical_fake_llm=True)
    manuscript = Manuscript(
        id=1,
        file_ref=str(tmp_path / "uploads" / ("1.pdf" if source_kind == "pdf" else "1.docx")),
        purged_at=None,
        section_tree={"source": "none", "nodes": []},
    )
    if source_kind == "pdf":
        payload = (FIXTURE_DIR / "native.pdf").read_bytes()
        final_path = Path(manuscript.file_ref)

        def read_source():
            return report.manuscript_file_path_for(manuscript, settings).read_bytes()

    else:
        original = Path(manuscript.file_ref)
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes((FIXTURE_DIR / "native.docx").read_bytes())
        extraction = ExtractionResult(
            page_count=0,
            anchor_kind="paragraph",
            image_only=False,
            text_chars=22,
            section_tree=SectionTree(source="none", nodes=[]),
            blocks=[
                TextBlock(text="Complete source text.", paragraph=0, max_font_size=11, bold_ratio=0)
            ],
            images=[],
        )
        payload = extraction.model_dump_json().encode("utf-8")
        final_path = ingestion.raw_store_path(settings, manuscript.id)

        def read_source():
            result = report.manuscript_paragraphs_for(manuscript, settings)
            assert result.paragraphs[0].text == "Complete source text."
            return final_path.read_bytes()

    durable = DurableBytes(payload)
    monkeypatch.setattr(source_files, "get_storage", lambda _settings: durable)
    monkeypatch.setattr(ingestion, "get_storage", lambda _settings: durable)
    written, release = threading.Event(), threading.Event()
    pause_first_writer(monkeypatch, written, release)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="cold-fill") as first_pool:
        first = first_pool.submit(read_source)
        try:
            assert written.wait(timeout=5), "First source read did not reach real disk write"
            with ThreadPoolExecutor(max_workers=1) as second_pool:
                second = second_pool.submit(read_source)
                assert second.result(timeout=5) == payload
            assert final_path.read_bytes() == payload
        finally:
            release.set()
        assert first.result(timeout=5) == payload
    expected = [final_path] if source_kind == "pdf" else [final_path, original]
    assert sorted(path for path in tmp_path.rglob("*") if path.is_file()) == sorted(expected)


def test_warm_cache_does_not_download_or_rewrite(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    final_path = tmp_path / "source.pdf"
    final_path.write_bytes(b"existing complete source")
    durable = DurableBytes(b"different object")
    assert ensure_local_file(settings, durable, str(final_path)) == final_path
    assert final_path.read_bytes() == b"existing complete source"
    assert durable.reads == 0


def test_late_cold_fill_preserves_already_published_file_identity(tmp_path, monkeypatch):
    """A delayed filler must not rename over a file another consumer is opening."""
    settings = Settings(_env_file=None, data_dir=tmp_path)
    final_path = tmp_path / "source.pdf"
    payload = b"complete private source"
    written, release = threading.Event(), threading.Event()
    pause_first_writer(monkeypatch, written, release)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="cold-fill") as pool:
        late = pool.submit(ensure_local_file, settings, DurableBytes(payload), str(final_path))
        try:
            assert written.wait(timeout=5)
            assert ensure_local_file(settings, DurableBytes(payload), str(final_path)) == final_path
            published = final_path.stat()
            release.set()
            assert late.result(timeout=5) == final_path
            assert final_path.stat().st_ino == published.st_ino
            assert final_path.read_bytes() == payload
        finally:
            release.set()
    assert [path for path in tmp_path.iterdir() if path.is_file()] == [final_path]


@pytest.mark.parametrize("failure", [FileNotFoundError, TimeoutError, PermissionError])
def test_durable_failure_retains_exception_and_creates_no_cache(tmp_path, failure):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    final_path = tmp_path / "uploads" / "source.pdf"

    class FailedStorage:
        def get_bytes(self, _key):
            raise failure("Controlled unavailable durable object")

    with pytest.raises(failure, match="Controlled unavailable"):
        ensure_local_file(settings, FailedStorage(), str(final_path))
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


@pytest.mark.parametrize("phase", ["write", "close", "publish"])
def test_failed_publication_cleans_private_file_without_publishing_partial_source(
    tmp_path, monkeypatch, phase
):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    final_path = tmp_path / "uploads" / "source.pdf"
    original_open = io.open
    created = []
    original_temporary = storage_module.NamedTemporaryFile

    class FailedWrite:
        def __init__(self, file):
            self.file = file

        def __getattr__(self, name):
            return getattr(self.file, name)

        def write(self, data):
            if phase == "write":
                self.file.write(data[:1])
                self.file.flush()
                raise OSError("Controlled write failure")
            return self.file.write(data)

        def close(self):
            self.file.close()
            if phase == "close":
                raise OSError("Controlled close failure")

    def controlled_open(*args, **kwargs):
        file = original_open(*args, **kwargs)
        mode = kwargs.get("mode", args[1] if len(args) > 1 else "r")
        return FailedWrite(file) if "w" in mode and "b" in mode else file

    def record_temporary(*args, **kwargs):
        temporary = original_temporary(*args, **kwargs)
        created.append(Path(temporary.name))
        return temporary

    def fail_publish(*_args):
        raise OSError("Controlled publish failure")

    monkeypatch.setattr(io, "open", controlled_open)
    monkeypatch.setattr(storage_module, "NamedTemporaryFile", record_temporary)
    if phase == "publish":
        monkeypatch.setattr(
            storage_module.os, "rename" if os.name == "nt" else "link", fail_publish
        )
    with pytest.raises(OSError, match=f"Controlled {phase} failure"):
        ensure_local_file(settings, DurableBytes(b"complete private source"), str(final_path))
    assert len(created) == 1
    assert created[0].parent == final_path.parent
    assert created[0] != final_path
    assert not created[0].exists()
    assert not final_path.exists()
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_source_is_closed_and_complete_before_publication(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    final_path = tmp_path / "source.pdf"
    payload = b"complete private source"
    original_temporary = storage_module.NamedTemporaryFile
    publication_name = "rename" if os.name == "nt" else "link"
    original_publish = getattr(os, publication_name)
    created = []

    def record_temporary(*args, **kwargs):
        temporary = original_temporary(*args, **kwargs)
        created.append(temporary)
        return temporary

    def observe_publish(source, destination):
        assert created[0].closed
        assert Path(source).parent == final_path.parent
        assert Path(source).read_bytes() == payload
        assert not final_path.exists()
        return original_publish(source, destination)

    monkeypatch.setattr(storage_module, "NamedTemporaryFile", record_temporary)
    monkeypatch.setattr(storage_module.os, publication_name, observe_publish)
    assert ensure_local_file(settings, DurableBytes(payload), str(final_path)) == final_path
    assert final_path.read_bytes() == payload
    assert not Path(created[0].name).exists()


def _spawned_fill(cache_dir, payload, first, written, release, results):
    """Each spawned worker imports its own storage module and uses real disk I/O."""

    def deny_network(*_args, **_kwargs):
        raise AssertionError("Spawned publication control must remain offline")

    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    original_open = io.open

    def controlled_open(*args, **kwargs):
        file = original_open(*args, **kwargs)
        mode = kwargs.get("mode", args[1] if len(args) > 1 else "r")
        if first and "w" in mode and "b" in mode:
            return PartialWrite(file, written, release)
        return file

    io.open = controlled_open
    try:
        settings = Settings(_env_file=None, data_dir=Path(cache_dir))
        final_path = Path(cache_dir) / "source.pdf"
        result = ensure_local_file(settings, DurableBytes(payload), str(final_path))
        results.put(("complete", result.read_bytes()))
    except BaseException as error:
        results.put(("error", type(error).__name__))
    finally:
        io.open = original_open


def test_independent_processes_publish_only_complete_source(tmp_path):
    context = multiprocessing.get_context("spawn")
    written, release, results = context.Event(), context.Event(), context.Queue()
    payload = b"complete private source bytes"
    workers = []
    try:
        first = context.Process(
            target=_spawned_fill, args=(str(tmp_path), payload, True, written, release, results)
        )
        first.start()
        workers.append(first)
        assert written.wait(timeout=15), "Spawned writer did not reach partial disk write"
        second = context.Process(
            target=_spawned_fill, args=(str(tmp_path), payload, False, written, release, results)
        )
        second.start()
        workers.append(second)
        assert results.get(timeout=15) == ("complete", payload)
        release.set()
        assert results.get(timeout=15) == ("complete", payload)
        for worker in workers:
            worker.join(timeout=15)
            assert worker.exitcode == 0
        assert [path for path in tmp_path.iterdir() if path.is_file()] == [tmp_path / "source.pdf"]
        assert (tmp_path / "source.pdf").read_bytes() == payload
    finally:
        release.set()
        for worker in workers:
            worker.join(timeout=5)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        results.close()
        results.join_thread()


@pytest.mark.parametrize("reader_kind", ["binary", "pdf"])
def test_competing_complete_fill_remains_usable_while_source_reader_is_open(
    tmp_path, monkeypatch, reader_kind
):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    final_path = tmp_path / "source.pdf"
    payload = (FIXTURE_DIR / "native.pdf").read_bytes()
    written, release = threading.Event(), threading.Event()
    pause_first_writer(monkeypatch, written, release)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="cold-fill") as pool:
        first = pool.submit(ensure_local_file, settings, DurableBytes(payload), str(final_path))
        try:
            assert written.wait(timeout=5)
            assert ensure_local_file(settings, DurableBytes(payload), str(final_path)) == final_path
            reader = final_path.open("rb") if reader_kind == "binary" else pymupdf.open(final_path)
            with reader:
                if reader_kind == "binary":
                    assert reader.read() == payload
                else:
                    assert reader.page_count > 0
                release.set()
                assert first.result(timeout=5) == final_path
        finally:
            release.set()
    assert [path for path in tmp_path.iterdir() if path.is_file()] == [final_path]


@pytest.mark.parametrize("competing_state", ["same", "different", "missing", "unreadable"])
def test_existing_destination_requires_exact_complete_competing_bytes(
    tmp_path, monkeypatch, competing_state
):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    final_path = tmp_path / "source.pdf"
    payload = b"complete private source"
    denied = FileExistsError("Controlled existing destination")
    original_read = Path.read_bytes

    def existing_destination(*_args):
        if competing_state != "missing":
            final_path.write_bytes(payload if competing_state == "same" else b"different source")
        raise denied

    def controlled_read(path):
        if path == final_path and competing_state == "unreadable":
            raise OSError("Controlled verification failure")
        return original_read(path)

    monkeypatch.setattr(
        storage_module.os, "rename" if os.name == "nt" else "link", existing_destination
    )
    monkeypatch.setattr(storage_module, "_read_published_winner", controlled_read)
    if competing_state == "same":
        assert ensure_local_file(settings, DurableBytes(payload), str(final_path)) == final_path
        assert original_read(final_path) == payload
    else:
        with pytest.raises(FileExistsError) as error:
            ensure_local_file(settings, DurableBytes(payload), str(final_path))
        assert error.value is denied
    assert sorted(path for path in tmp_path.iterdir() if path.is_file()) == (
        [] if competing_state == "missing" else [final_path]
    )


def test_permission_failure_is_not_masked_by_matching_destination(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    final_path = tmp_path / "source.pdf"
    payload = b"complete private source"
    denied = PermissionError("Controlled permission failure")

    def denied_publish(*_args):
        final_path.write_bytes(payload)
        raise denied

    monkeypatch.setattr(storage_module.os, "rename" if os.name == "nt" else "link", denied_publish)
    with pytest.raises(PermissionError) as error:
        ensure_local_file(settings, DurableBytes(payload), str(final_path))
    assert error.value is denied
    assert list(tmp_path.iterdir()) == [final_path]


@pytest.mark.parametrize("source_kind", ["pdf", "extraction"])
def test_simultaneous_cold_source_consumers_keep_published_winner_readable(
    tmp_path, monkeypatch, source_kind
):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    manuscript = Manuscript(
        id=1,
        file_ref=str(tmp_path / "uploads" / ("1.pdf" if source_kind == "pdf" else "1.docx")),
        purged_at=None,
        section_tree={"source": "none", "nodes": []},
    )
    barrier = threading.Barrier(6)
    if source_kind == "pdf":
        payload = (FIXTURE_DIR / "native.pdf").read_bytes()
        final_path = Path(manuscript.file_ref)

        def consume():
            path = report.manuscript_file_path_for(manuscript, settings)
            with pymupdf.open(path) as document:
                assert document.page_count > 0
            assert path.read_bytes() == payload

    else:
        original = Path(manuscript.file_ref)
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes((FIXTURE_DIR / "native.docx").read_bytes())
        extraction = ExtractionResult(
            page_count=0,
            anchor_kind="paragraph",
            image_only=False,
            text_chars=22,
            section_tree=SectionTree(source="none", nodes=[]),
            blocks=[
                TextBlock(text="Complete source text.", paragraph=0, max_font_size=11, bold_ratio=0)
            ],
            images=[],
        )
        payload = extraction.model_dump_json().encode("utf-8")
        final_path = ingestion.raw_store_path(settings, manuscript.id)

        def consume():
            result = report.manuscript_paragraphs_for(manuscript, settings)
            assert result.paragraphs[0].text == "Complete source text."

    class SimultaneousStorage:
        def get_bytes(self, _key):
            barrier.wait(timeout=5)
            return payload

    durable = SimultaneousStorage()
    monkeypatch.setattr(source_files, "get_storage", lambda _settings: durable)
    monkeypatch.setattr(ingestion, "get_storage", lambda _settings: durable)
    with ThreadPoolExecutor(max_workers=6) as pool:
        readers = [pool.submit(consume) for _ in range(6)]
        for reader in readers:
            reader.result(timeout=10)
    assert final_path.read_bytes() == payload
    expected = [final_path] if source_kind == "pdf" else [final_path, original]
    assert sorted(path for path in tmp_path.rglob("*") if path.is_file()) == sorted(expected)
