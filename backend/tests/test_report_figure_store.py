"""Report views read figure readings from their small store, not the whole extraction.

Each test compares against what a report showed before the store existed:
the readings taken from the whole extraction, byte for byte. Storage failures
are produced by the real R2 adapter, so the exceptions are the ones boto3
raises in production.
"""

import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from boto3.exceptions import S3UploadFailedError
from boto3.s3.transfer import TransferConfig
from botocore.stub import Stubber
from sqlalchemy.exc import OperationalError

import app.archive.service as archive
import app.ingest.service as service
import app.report.service as report_service
from app.ingest.figures import saved_figure_readings
from app.ingest.schemas import ExtractionResult, FigureSource
from app.ingest.service import (
    _write_raw_store,
    figure_store_path,
    keep_figure_store,
    load_figure_source,
    raw_store_path,
)
from app.storage import R2Storage, storage_key_for
from tests.test_docx_media_read import saved as saved
from tests.test_ingest_figures import Scripted, read


class Durable:
    """One object store behind every storage handle, as R2 is in production."""

    def __init__(self):
        self.objects = {}

    def put_file(self, local_path, key):
        self.objects[key] = Path(local_path).read_bytes()

    def download_to(self, key, target, *, chunk_bytes):
        if key not in self.objects:
            raise FileNotFoundError(key)
        target.write(self.objects[key])

    def delete(self, key):
        self.objects.pop(key, None)

    def close(self):
        pass


def real_r2(*answers):
    """Real R2 adapters, one per storage handle, each answering one S3 call
    with the next planned error: (operation, error code, HTTP status). A handle
    opened after the plan runs out answers nothing, so any S3 call it makes
    fails the test."""
    planned = list(answers)

    def handle(settings, **_):
        storage = R2Storage(
            bucket="figures",
            endpoint_url="https://r2.example.invalid",
            access_key_id="test",
            secret_access_key="test",
            client_config=None,
            transfer_config=TransferConfig(use_threads=False),
            namespace=None,
        )
        stubber = Stubber(storage._client)
        if planned:
            operation, code, status = planned.pop(0)
            stubber.add_client_error(operation, service_error_code=code, http_status_code=status)
        stubber.activate()
        return storage

    return handle


class Transaction:
    """The request's transaction: each purge-state query answers in turn,
    or fails as a lost connection does."""

    def __init__(self, *answers):
        self.answers = list(answers)

    async def scalar(self, statement):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


LOST = OperationalError("SELECT", {}, ConnectionResetError("connection lost"))


def _shown(readings):
    return None if readings is None else [entry.model_dump_json() for entry in readings]


def _from_whole_extraction(settings, manuscript):
    extraction = ExtractionResult.model_validate_json(
        raw_store_path(settings, manuscript.id).read_text(encoding="utf-8")
    )
    return _shown(saved_figure_readings(extraction, manuscript.content_hash))


def _from_store(settings, manuscript):
    source, _ = load_figure_source(settings, manuscript.id)
    return _shown(saved_figure_readings(source, manuscript.content_hash))


def _count_parses(monkeypatch, delay=0.0):
    parses = []
    real = service.load_raw_store

    def counting(settings, manuscript_id):
        parses.append(manuscript_id)
        time.sleep(delay)
        return real(settings, manuscript_id)

    monkeypatch.setattr(service, "load_raw_store", counting)
    return parses


def _as_older_record(settings, manuscript):
    """Prepared before figure stores existed: only the extraction was kept."""
    figure_store_path(settings, manuscript.id).unlink()


@pytest.fixture()
async def prepared(saved):
    """A saved DOCX with one available figure reading, stored as preparation stores it."""
    await read(saved, Scripted())
    settings, manuscript, extraction, _ = saved
    _write_raw_store(extraction, raw_store_path(settings, manuscript.id))
    return saved


async def test_report_views_read_the_store_and_never_parse_the_extraction(prepared, monkeypatch):
    settings, manuscript, extraction, _ = prepared
    kept = FigureSource.model_validate_json(figure_store_path(settings, manuscript.id).read_bytes())
    # Every reading, but only the image the available one points at.
    assert len(kept.figure_readings) == len(extraction.images) == 2
    assert list(kept.images) == [0]
    before = _from_whole_extraction(settings, manuscript)
    assert json.loads(before[0])["status"] == "available"
    parses = _count_parses(monkeypatch)
    for _ in range(3):
        assert _from_store(settings, manuscript) == before
        assert load_figure_source(settings, manuscript.id)[1] is False
    assert parses == []


async def test_an_older_record_is_parsed_once_and_its_store_kept(prepared, monkeypatch):
    settings, manuscript, _, _ = prepared
    before = _from_whole_extraction(settings, manuscript)
    _as_older_record(settings, manuscript)
    parses = _count_parses(monkeypatch)
    first, derived = load_figure_source(settings, manuscript.id)
    assert derived is True
    assert _shown(saved_figure_readings(first, manuscript.content_hash)) == before
    assert [_from_store(settings, manuscript) for _ in range(2)] == [before] * 2
    assert parses == [manuscript.id]
    kept = FigureSource.model_validate_json(figure_store_path(settings, manuscript.id).read_bytes())
    assert _shown(saved_figure_readings(kept, manuscript.content_hash)) == before


async def test_the_kept_store_survives_a_restart_through_durable_storage(prepared, monkeypatch):
    settings, manuscript, _, _ = prepared
    before = _from_whole_extraction(settings, manuscript)
    _as_older_record(settings, manuscript)
    durable = Durable()
    trips = []

    class Handle:
        def __init__(self, bounded):
            self.bounded = bounded

        def put_file(self, local_path, key):
            trips.append((key, self.bounded))
            durable.put_file(local_path, key)

        def download_to(self, key, target, *, chunk_bytes):
            trips.append((key, self.bounded))
            durable.download_to(key, target, chunk_bytes=chunk_bytes)

        def close(self):
            pass

    monkeypatch.setattr(
        service, "get_storage", lambda settings, *, figure_store=False: Handle(figure_store)
    )
    parses = _count_parses(monkeypatch)
    assert _from_store(settings, manuscript) == before
    keep_figure_store(settings, manuscript.id)
    store = figure_store_path(settings, manuscript.id)
    key = storage_key_for(settings, str(store))
    assert key in durable.objects
    # A restart empties the local disk; the durable copy is read back instead.
    store.unlink()
    assert _from_store(settings, manuscript) == before
    assert parses == [manuscript.id]
    # Fetch miss, keep, fetch after the restart, each with the figure store's
    # own short timeouts. The re-check under the lock looks only on local disk.
    assert trips == [(key, True), (key, True), (key, True)]


async def test_a_store_from_an_earlier_extraction_is_never_shown(prepared, monkeypatch):
    settings, manuscript, extraction, _ = prepared
    store = figure_store_path(settings, manuscript.id)
    earlier = store.read_bytes()
    # Prepared again, and the reading is no longer available. The figure store
    # left from the earlier extraction still says it is.
    extraction.figure_readings[0].visible_content = None
    _write_raw_store(extraction, raw_store_path(settings, manuscript.id))
    store.write_bytes(earlier)
    after = _from_whole_extraction(settings, manuscript)
    assert json.loads(after[0])["status"] == "unavailable"
    parses = _count_parses(monkeypatch)
    assert _from_store(settings, manuscript) == after
    assert _from_store(settings, manuscript) == after
    assert parses == [manuscript.id]


async def test_an_earlier_durable_copy_is_never_shown(prepared, monkeypatch):
    settings, manuscript, extraction, _ = prepared
    store = figure_store_path(settings, manuscript.id)
    key = storage_key_for(settings, str(store))
    durable = Durable()
    durable.objects[key] = store.read_bytes()
    monkeypatch.setattr(service, "get_storage", lambda settings, **_: durable)
    extraction.figure_readings[0].visible_content = None
    _write_raw_store(extraction, raw_store_path(settings, manuscript.id))
    store.unlink()
    after = _from_whole_extraction(settings, manuscript)
    parses = _count_parses(monkeypatch)
    assert _from_store(settings, manuscript) == after
    assert parses == [manuscript.id]
    keep_figure_store(settings, manuscript.id)
    current = hashlib.sha256(raw_store_path(settings, manuscript.id).read_bytes()).hexdigest()
    assert FigureSource.model_validate_json(durable.objects[key]).extraction_sha256 == current


async def test_a_store_out_of_reach_never_fails_the_view(prepared, monkeypatch):
    settings, manuscript, _, _ = prepared
    before = _from_whole_extraction(settings, manuscript)
    figure_store_path(settings, manuscript.id).unlink()
    monkeypatch.setattr(
        service,
        "get_storage",
        real_r2(("get_object", "ServiceUnavailable", 503), ("get_object", "SlowDown", 503)),
    )
    parses = _count_parses(monkeypatch)
    source, derived = load_figure_source(settings, manuscript.id)
    assert _shown(saved_figure_readings(source, manuscript.content_hash)) == before
    assert derived is True
    assert parses == [manuscript.id]


def test_the_real_adapter_reports_a_refused_put_as_s3uploadfailederror(prepared, monkeypatch):
    """What boto3 raises, so every caller that keeps a store can catch it."""
    settings, manuscript, _, _ = prepared
    monkeypatch.setattr(service, "get_storage", real_r2(("put_object", "ServiceUnavailable", 503)))
    with pytest.raises(S3UploadFailedError):
        keep_figure_store(settings, manuscript.id)


async def test_a_damaged_store_is_rebuilt_from_the_extraction(prepared, monkeypatch):
    settings, manuscript, _, _ = prepared
    before = _from_whole_extraction(settings, manuscript)
    store = figure_store_path(settings, manuscript.id)
    store.write_text('{"figure_readings": [{"status"', encoding="utf-8")
    parses = _count_parses(monkeypatch)
    assert _from_store(settings, manuscript) == before
    assert _from_store(settings, manuscript) == before
    assert parses == [manuscript.id]


async def test_two_first_views_of_an_older_record_parse_it_once(prepared, monkeypatch):
    settings, manuscript, _, _ = prepared
    before = _from_whole_extraction(settings, manuscript)
    _as_older_record(settings, manuscript)
    parses = _count_parses(monkeypatch, delay=0.3)
    shown = []
    views = [
        threading.Thread(target=lambda: shown.append(_from_store(settings, manuscript)))
        for _ in range(2)
    ]
    for view in views:
        view.start()
    for view in views:
        view.join(timeout=30)
    assert shown == [before, before]
    assert parses == [manuscript.id]


async def test_a_view_still_shows_readings_when_the_store_cannot_be_written(prepared, monkeypatch):
    settings, manuscript, _, _ = prepared
    before = _from_whole_extraction(settings, manuscript)
    _as_older_record(settings, manuscript)

    def disk_full(source, path):
        raise OSError("No space left on device")

    monkeypatch.setattr(service, "_write_figure_store", disk_full)
    parses = _count_parses(monkeypatch)
    for _ in range(2):
        source, derived = load_figure_source(settings, manuscript.id)
        assert _shown(saved_figure_readings(source, manuscript.content_hash)) == before
        assert derived is False
    assert parses == [manuscript.id, manuscript.id]


async def test_a_derived_store_is_kept_while_the_source_lock_holds(prepared, monkeypatch):
    settings, manuscript, _, _ = prepared
    _as_older_record(settings, manuscript)
    load_figure_source(settings, manuscript.id)
    durable = Durable()
    monkeypatch.setattr(service, "get_storage", lambda settings, **_: durable)
    await report_service._keep_figure_store(Transaction(None, None), settings, manuscript.id)
    store = figure_store_path(settings, manuscript.id)
    assert storage_key_for(settings, str(store)) in durable.objects
    assert store.is_file()


@pytest.mark.parametrize(
    "answers",
    [
        pytest.param((datetime.now(UTC),), id="purged before the keep"),
        pytest.param((LOST,), id="connection lost before the keep"),
        pytest.param((None, datetime.now(UTC)), id="purged during the keep"),
        pytest.param((None, LOST), id="connection lost during the keep"),
    ],
)
async def test_a_purged_manuscript_never_keeps_its_figure_store(prepared, monkeypatch, answers):
    """The store holds the thesis's figure text. If the source lock cannot be
    confirmed around the put, nothing of it may stay behind."""
    settings, manuscript, _, _ = prepared
    _as_older_record(settings, manuscript)
    load_figure_source(settings, manuscript.id)
    durable = Durable()
    monkeypatch.setattr(service, "get_storage", lambda settings, **_: durable)
    await report_service._keep_figure_store(Transaction(*answers), settings, manuscript.id)
    assert durable.objects == {}
    assert not figure_store_path(settings, manuscript.id).exists()


async def test_a_refused_put_keeps_the_local_copy_and_fails_nothing(prepared, monkeypatch):
    settings, manuscript, _, _ = prepared
    before = _from_whole_extraction(settings, manuscript)
    _as_older_record(settings, manuscript)
    load_figure_source(settings, manuscript.id)
    monkeypatch.setattr(service, "get_storage", real_r2(("put_object", "ServiceUnavailable", 503)))
    await report_service._keep_figure_store(Transaction(None, None), settings, manuscript.id)
    assert figure_store_path(settings, manuscript.id).is_file()
    assert _from_store(settings, manuscript) == before


@pytest.mark.parametrize("older", [False, True])
@pytest.mark.parametrize(
    "mutation",
    ["document", "occurrence", "part", "empty_content", "empty_hash", "locator", "no_image"],
)
async def test_stale_readings_stay_unavailable_through_the_store(saved, mutation, older):
    await read(saved, Scripted())
    settings, manuscript, extraction, _ = saved
    entry = extraction.figure_readings[0]
    if mutation == "document":
        entry.source_binding["document_sha256"] = "f" * 64
    elif mutation == "occurrence":
        entry.source_binding["image_index"] = 1
    elif mutation == "part":
        entry.source_binding["locator"]["docx_source"]["part_name"] = "word/media/other.png"
    elif mutation == "empty_hash":
        entry.source_binding["image_sha256"] = ""
    elif mutation == "locator":
        entry.source_binding["locator"] = None
    elif mutation == "no_image":
        # The reading points past the last image the extraction has.
        entry.image_index = len(extraction.images) + 3
        entry.source_binding["image_index"] = entry.image_index
    else:
        entry.visible_content = None
    _write_raw_store(extraction, raw_store_path(settings, manuscript.id))
    if older:
        _as_older_record(settings, manuscript)
    before = _from_whole_extraction(settings, manuscript)
    assert json.loads(before[0])["status"] == "unavailable"
    assert _from_store(settings, manuscript) == before


@pytest.mark.parametrize("older", [False, True])
async def test_an_extraction_without_figure_readings_shows_none(saved, older):
    settings, manuscript, extraction, _ = saved
    extraction.figure_readings = None
    _write_raw_store(extraction, raw_store_path(settings, manuscript.id))
    if older:
        _as_older_record(settings, manuscript)
    assert _from_whole_extraction(settings, manuscript) is None
    assert _from_store(settings, manuscript) is None


async def test_purge_deletes_the_store_locally_and_durably(prepared, monkeypatch):
    settings, manuscript, _, _ = prepared
    store = figure_store_path(settings, manuscript.id)
    durable = Durable()
    key = storage_key_for(settings, str(store))
    durable.objects[key] = store.read_bytes()
    monkeypatch.setattr(archive, "get_storage", lambda settings, **_: durable)
    archive.delete_stored_files(manuscript, settings)
    assert not store.exists()
    assert not raw_store_path(settings, manuscript.id).exists()
    assert key not in durable.objects


async def test_a_failed_cleanup_still_removes_the_local_copy(prepared, monkeypatch):
    settings, manuscript, _, _ = prepared
    _as_older_record(settings, manuscript)
    load_figure_source(settings, manuscript.id)
    monkeypatch.setattr(
        service, "get_storage", real_r2(("delete_object", "ServiceUnavailable", 503))
    )
    await report_service._keep_figure_store(Transaction(LOST), settings, manuscript.id)
    assert not figure_store_path(settings, manuscript.id).exists()


async def test_a_cancelled_view_still_finishes_the_keep_and_its_cleanup(prepared, monkeypatch):
    """The put runs in a thread that cannot be stopped, so a cancelled view
    waits for it and for the cleanup instead of releasing the lock early."""
    import asyncio

    settings, manuscript, _, _ = prepared
    _as_older_record(settings, manuscript)
    load_figure_source(settings, manuscript.id)
    durable = Durable()
    entered, release = threading.Event(), threading.Event()
    real_put = durable.put_file

    def slow_put(local_path, key):
        entered.set()
        release.wait(10)
        real_put(local_path, key)

    durable.put_file = slow_put
    monkeypatch.setattr(service, "get_storage", lambda settings, **_: durable)
    view = asyncio.ensure_future(
        report_service._finish_even_if_cancelled(
            report_service._keep_figure_store(Transaction(None, LOST), settings, manuscript.id)
        )
    )
    await asyncio.get_running_loop().run_in_executor(None, entered.wait, 10)
    view.cancel()
    await asyncio.sleep(0.1)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await view
    # The lock could not be confirmed after the put, so nothing stays behind.
    assert durable.objects == {}
    assert not figure_store_path(settings, manuscript.id).exists()


async def test_a_damaged_store_is_reported_in_the_log(prepared, monkeypatch, caplog):
    settings, manuscript, _, _ = prepared
    figure_store_path(settings, manuscript.id).write_text("{", encoding="utf-8")
    with caplog.at_level("WARNING"):
        load_figure_source(settings, manuscript.id)
    assert "could not be read" in caplog.text


async def test_a_cancelled_report_view_does_not_release_the_lock_mid_keep(prepared, monkeypatch):
    """Through attach_report_figures itself: cancelling the view still waits
    for the keep and its cleanup."""
    import asyncio
    from types import SimpleNamespace

    settings, manuscript, _, _ = prepared
    _as_older_record(settings, manuscript)
    source, _ = load_figure_source(settings, manuscript.id)
    durable = Durable()
    entered, release = threading.Event(), threading.Event()
    real_put = durable.put_file

    def slow_put(local_path, key):
        entered.set()
        release.wait(10)
        real_put(local_path, key)

    durable.put_file = slow_put
    monkeypatch.setattr(service, "get_storage", lambda settings, **_: durable)

    async def derived_now(session, row, reader):
        return source, True

    monkeypatch.setattr(report_service, "read_source", derived_now)

    class Session(Transaction):
        async def get(self, model, key, **kwargs):
            return manuscript

    report = SimpleNamespace(
        manuscript_id=manuscript.id, model_copy=lambda update: SimpleNamespace(**update)
    )
    view = asyncio.ensure_future(
        report_service.attach_report_figures(Session(None, LOST), report, settings)
    )
    await asyncio.get_running_loop().run_in_executor(None, entered.wait, 10)
    view.cancel()
    await asyncio.sleep(0.1)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await view
    assert durable.objects == {}
    assert not figure_store_path(settings, manuscript.id).exists()


async def test_cleanup_uses_the_retrying_storage_client(prepared, monkeypatch):
    """A purge may already have happened, so the cleanup must not use the
    short single-attempt figure-store client."""
    settings, manuscript, _, _ = prepared
    durable, clients = Durable(), []

    def storage(settings, **kwargs):
        clients.append(kwargs.get("figure_store", False))
        return durable

    monkeypatch.setattr(service, "get_storage", storage)
    service.discard_figure_store(settings, manuscript.id)
    assert clients == [False]


async def test_a_stuck_local_file_does_not_keep_the_durable_copy(prepared, monkeypatch):
    settings, manuscript, _, _ = prepared
    store = figure_store_path(settings, manuscript.id)
    key = storage_key_for(settings, str(store))
    durable = Durable()
    durable.objects[key] = store.read_bytes()
    monkeypatch.setattr(service, "get_storage", lambda settings, **_: durable)
    real_unlink = Path.unlink

    def locked(self, *args, **kwargs):
        if self == store:
            raise PermissionError("in use")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    with pytest.raises(PermissionError):
        service.discard_figure_store(settings, manuscript.id)
    assert key not in durable.objects
