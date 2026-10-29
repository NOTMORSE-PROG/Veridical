"""BUG-527: large uploads move their bytes through the small host at most once.

The staged object becomes the durable original by a server-side copy, the
local cache is written once and hashed while it is written, every later hash
of the unchanged file is reused, the relay stores the request body in parts
while it is still arriving, and the browser can send parts straight to
storage. `FakeR2` keeps the real `R2Storage` method contract in memory.
"""

import asyncio
import hashlib
import os
import random
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

import app.auth.service as auth_service
from app.auth.security import hash_password
from app.config import Settings, get_settings
from app.errors import ConflictError
from app.ingest import compute_client, direct_upload, source_digest
from app.ingest.compute_contract import (
    SIGNATURE_HEADER,
    canonical_json,
    decode_job,
    signature,
)
from app.ingest.compute_native import execute_native_job
from app.ingest.service import StagedSource, place_staged_source
from app.storage import R2Storage

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)

SCRATCH_DB = "veridical_bug527test"
MIB = 1024 * 1024


def _no_such_upload(operation):
    return ClientError({"Error": {"Code": "NoSuchUpload", "Message": "gone"}}, operation)


class FakeR2(R2Storage):
    """In-memory bucket with the R2Storage method contract used by uploads."""

    def __init__(self, *, part_delay=0.0):
        self.objects: dict[str, bytes] = {}
        self.etags: dict[str, str] = {}
        self.uploads: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.part_delay = part_delay
        self.fail_part = None
        self.fail_copy = False
        self.lock = threading.Lock()

    def _require_scope(self, key):
        pass

    def _store(self, key, data):
        self.objects[key] = data
        self.etags[key] = f'"{hashlib.md5(data).hexdigest()}"'  # noqa: S324 -- test etag only

    def create_multipart(self, key):
        upload_id = uuid4().hex
        with self.lock:
            self.uploads[upload_id] = {"key": key, "parts": {}}
            self.calls.append(("create_multipart", key))
        return upload_id

    def upload_part(self, key, upload_id, number, body):
        if self.part_delay:
            threading.Event().wait(self.part_delay)
        if self.fail_part == number:
            raise OSError("simulated part failure")
        return self.put_part(key, upload_id, number, bytes(body))

    def put_part(self, key, upload_id, number, body):
        with self.lock:
            upload = self.uploads.get(upload_id)
            if upload is None or upload["key"] != key:
                raise _no_such_upload("UploadPart")
            etag = f'"{hashlib.md5(body).hexdigest()}"'  # noqa: S324 -- test etag only
            upload["parts"][number] = (body, etag)
            self.calls.append(("upload_part", key, number, len(body)))
        return etag

    def presign_part(self, key, upload_id, number, length, ttl):
        return f"https://r2.invalid/{key}?uploadId={upload_id}&partNumber={number}&len={length}"

    def list_parts(self, key, upload_id):
        with self.lock:
            upload = self.uploads.get(upload_id)
            if upload is None:
                raise _no_such_upload("ListParts")
            return [
                {"PartNumber": n, "Size": len(body), "ETag": etag}
                for n, (body, etag) in sorted(upload["parts"].items())
            ]

    def complete_multipart(self, key, upload_id, parts):
        with self.lock:
            upload = self.uploads.pop(upload_id, None)
            if upload is None:
                raise _no_such_upload("CompleteMultipartUpload")
            data = b"".join(
                upload["parts"][p["PartNumber"]][0]
                for p in sorted(parts, key=lambda p: p["PartNumber"])
            )
            self._store(key, data)
            self.calls.append(("complete_multipart", key, len(parts)))

    def abort_multipart(self, key, upload_id):
        with self.lock:
            self.uploads.pop(upload_id, None)
            self.calls.append(("abort_multipart", key))

    def object_metadata(self, key):
        return {"ContentLength": len(self.objects[key]), "ETag": self.etags[key]}

    def copy(self, source_key, key, *, etag=None):
        if self.fail_copy:
            raise OSError("simulated copy failure")
        if etag is not None and self.etags[source_key] != etag:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "CopyObject")
        with self.lock:
            self._store(key, self.objects[source_key])
            self.calls.append(("copy", source_key, key, etag))

    def download_to(self, key, target, *, chunk_bytes, etag=None):
        if etag is not None and self.etags[key] != etag:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "GetObject")
        self.calls.append(("download_to", key, etag))
        data = self.objects[key]
        for offset in range(0, len(data), chunk_bytes):
            target.write(data[offset : offset + chunk_bytes])

    def put_file(self, local_path, key):
        self.calls.append(("put_file", key))
        self._store(key, Path(local_path).read_bytes())

    def upload_stream(self, source, key):
        self.calls.append(("upload_stream", key))
        self._store(key, source.read())

    def presign_upload(self, key, length, ttl):
        return f"https://r2.invalid/{key}?len={length}"

    def get_bytes(self, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def delete(self, key):
        self.objects.pop(key, None)
        self.etags.pop(key, None)

    def exists(self, key):
        return key in self.objects

    def delete_prefix(self, prefix):
        for key in [key for key in self.objects if key.startswith(prefix)]:
            self.delete(key)

    def close(self):
        pass


def _settings(tmp_path, **overrides):
    values = {
        "_env_file": None,
        "data_dir": tmp_path,
        "storage_transfer_chunk_bytes": 5 * MIB,
        "direct_upload_part_mb": 5,
    }
    values.update(overrides)
    return Settings(**values)


# --- whole-file digest reuse -------------------------------------------------


def test_file_sha256_reads_an_unchanged_file_once_and_again_after_a_rewrite(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    path = tmp_path / "source.pdf"
    path.write_bytes(b"%PDF-1.7 first" * 1000)
    reads = []
    real = hashlib.file_digest

    def counting(fileobj, digest):
        reads.append(fileobj.name)
        return real(fileobj, digest)

    monkeypatch.setattr(source_digest.hashlib, "file_digest", counting)
    first = source_digest.file_sha256(path, settings)
    for _ in range(14):
        assert source_digest.file_sha256(path, settings) == first
    assert len(reads) == 1
    path.write_bytes(b"%PDF-1.7 second" * 1000)
    assert (
        source_digest.file_sha256(path, settings) == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert len(reads) == 2


def test_recorded_digest_is_bound_to_the_file_it_describes(tmp_path):
    settings = _settings(tmp_path)
    path = tmp_path / "written.pdf"
    path.write_bytes(b"streamed bytes")
    source_digest.record_file_sha256(path, "recorded-while-writing", settings)
    assert source_digest.file_sha256(path, settings) == "recorded-while-writing"
    path.write_bytes(b"streamed bytes, then changed")
    assert (
        source_digest.file_sha256(path, settings) == hashlib.sha256(path.read_bytes()).hexdigest()
    )


def test_digest_memo_is_bounded(tmp_path):
    settings = _settings(tmp_path, source_digest_memo_entries=3)
    for index in range(6):
        path = tmp_path / f"{index}.bin"
        path.write_bytes(bytes([index]) * 10)
        source_digest.file_sha256(path, settings)
    assert len(source_digest._memo) <= 3


# --- staged source placement -------------------------------------------------


def _staged(store, data, key="upload_staging/abc"):
    store._store(key, data)
    return StagedSource(key=key, etag=store.etags[key], size=len(data))


def test_staged_source_is_copied_inside_storage_and_downloaded_once(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    store = FakeR2()
    data = os.urandom(3 * MIB + 17)
    staged = _staged(store, data)
    dest = tmp_path / "uploads" / "7.pdf"
    reads = []
    real = hashlib.file_digest
    monkeypatch.setattr(
        source_digest.hashlib,
        "file_digest",
        lambda fileobj, digest: reads.append(fileobj.name) or real(fileobj, digest),
    )

    digest = place_staged_source(store, staged, dest, settings)

    assert digest == hashlib.sha256(data).hexdigest()
    assert dest.read_bytes() == data
    assert store.objects["uploads/7.pdf"] == data
    kinds = [call[0] for call in store.calls]
    assert kinds.count("download_to") == 1 and "put_file" not in kinds
    assert ("copy", staged.key, "uploads/7.pdf", staged.etag) in store.calls
    assert ("download_to", staged.key, staged.etag) in store.calls
    # The native job and every crop check reuse the digest written above.
    for _ in range(5):
        assert source_digest.file_sha256(dest, settings) == digest
    assert reads == []


def test_relay_copy_on_this_instance_is_renamed_not_downloaded(tmp_path):
    settings = _settings(tmp_path)
    store = FakeR2()
    data = os.urandom(MIB + 3)
    local = tmp_path / "upload_staging" / str(uuid4())
    local.parent.mkdir(parents=True)
    local.write_bytes(data)
    source_digest.record_file_sha256(local, hashlib.sha256(data).hexdigest(), settings)
    staged = _staged(store, data)
    staged = StagedSource(key=staged.key, etag=staged.etag, size=staged.size, local=local)
    dest = tmp_path / "uploads" / "8.pdf"

    digest = place_staged_source(store, staged, dest, settings)

    assert digest == hashlib.sha256(data).hexdigest()
    assert dest.read_bytes() == data and not local.exists()
    assert [call[0] for call in store.calls] == ["copy"]


def test_a_relay_copy_with_the_wrong_size_is_ignored(tmp_path):
    settings = _settings(tmp_path)
    store = FakeR2()
    data = os.urandom(MIB)
    local = tmp_path / "upload_staging" / "stale"
    local.parent.mkdir(parents=True)
    local.write_bytes(data[:-1])
    staged = _staged(store, data)
    staged = StagedSource(key=staged.key, etag=staged.etag, size=staged.size, local=local)
    dest = tmp_path / "uploads" / "9.pdf"

    assert place_staged_source(store, staged, dest, settings) == hashlib.sha256(data).hexdigest()
    assert dest.read_bytes() == data
    assert "download_to" in [call[0] for call in store.calls]


def test_failed_copy_leaves_no_local_original_behind(tmp_path):
    settings = _settings(tmp_path)
    store = FakeR2()
    store.fail_copy = True
    staged = _staged(store, os.urandom(MIB))
    dest = tmp_path / "uploads" / "10.pdf"
    with pytest.raises(OSError, match="copy failure"):
        place_staged_source(store, staged, dest, settings)
    assert not dest.exists()
    assert not list((tmp_path / "uploads").glob("tmp*"))


def test_failed_download_still_waits_for_the_copy(tmp_path):
    settings = _settings(tmp_path)
    store = FakeR2()
    staged = _staged(store, os.urandom(MIB))
    finished = []
    real_copy = store.copy

    def slow_copy(*args, **kwargs):
        threading.Event().wait(0.3)
        real_copy(*args, **kwargs)
        finished.append(True)

    store.copy = slow_copy
    store.download_to = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("network"))
    dest = tmp_path / "uploads" / "11.pdf"
    with pytest.raises(OSError, match="network"):
        place_staged_source(store, staged, dest, settings)
    assert finished == [True]
    assert not dest.exists()


# --- crop checks reuse one source hash ---------------------------------------


def test_many_crops_hash_the_source_once(tmp_path, monkeypatch):
    source = tmp_path / "uploads" / "1.pdf"
    source.parent.mkdir()
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 40), "Results and analysis", fontsize=18)
        for row in range(3):
            pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 120, 120), False)
            pix.clear_with(60 + row * 50)
            page.insert_image(pymupdf.Rect(50, 80 + row * 230, 260, 290 + row * 230), pixmap=pix)
        doc.save(source)
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        native_compute_url="https://native.example/api/native_extract",
        native_compute_secret="private-test-credential-" * 3,
    )

    class ObjectStore:
        def __init__(self):
            self.objects = {"uploads/1.pdf": source.read_bytes()}

        def download_to(self, key, target, *, chunk_bytes):
            data = self.objects[key]
            for offset in range(0, len(data), chunk_bytes):
                target.write(data[offset : offset + chunk_bytes])

        def put_file(self, path, key):
            self.objects[key] = Path(path).read_bytes()

        def close(self):
            pass

    store = ObjectStore()

    def invoke(_url, *, content, headers, **_):
        import httpx

        job = decode_job(content, headers[SIGNATURE_HEADER], settings)
        receipt = execute_native_job(job, settings, store)
        return httpx.Response(
            200,
            json={
                "receipt": receipt,
                "signature": signature(canonical_json(receipt), settings.native_compute_secret),
            },
        )

    monkeypatch.setattr(compute_client.httpx, "post", invoke)
    monkeypatch.setattr(compute_client, "get_storage", lambda _: store)
    reads = []
    real = hashlib.file_digest

    def counting(fileobj, digest):
        if Path(fileobj.name) == source:
            reads.append(fileobj.name)
        return real(fileobj, digest)

    monkeypatch.setattr(source_digest.hashlib, "file_digest", counting)
    result = compute_client.extract_remote(str(source), settings, owner_id=1)
    assert len(result.images) == 3
    for _ in range(4):
        for image in result.images:
            png, binding = compute_client.remote_raster(source, image, settings)
            assert png.startswith(b"\x89PNG")
            assert binding["document_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert len(reads) == 1


# --- relay streaming ---------------------------------------------------------


class StreamRequest:
    def __init__(self, chunks, *, disconnect_after=None):
        self._chunks = chunks
        self._disconnect_after = disconnect_after
        self.consumed = 0

    async def stream(self):
        for index, chunk in enumerate(self._chunks):
            if self._disconnect_after is not None and index == self._disconnect_after:
                raise ClientDisconnect()
            self.consumed += len(chunk)
            await asyncio.sleep(0)
            yield chunk


def _chunks(data, size=64 * 1024):
    return [data[offset : offset + size] for offset in range(0, len(data), size)]


def _relay(tmp_path, monkeypatch, store, request, size, **overrides):
    settings = _settings(tmp_path, **overrides)
    monkeypatch.setattr(direct_upload, "get_storage", lambda _settings: store)
    return asyncio.run(direct_upload._stream_relay(request, "upload_staging/k", size, settings))


def test_relay_stores_equal_parts_while_the_body_arrives(tmp_path, monkeypatch):
    store = FakeR2()
    data = os.urandom(12 * MIB + 5)
    metadata, digest, partial = _relay(
        tmp_path, monkeypatch, store, StreamRequest(_chunks(data)), len(data)
    )
    assert store.objects["upload_staging/k"] == data
    assert metadata["ContentLength"] == len(data)
    assert digest == hashlib.sha256(data).hexdigest()
    assert partial.read_bytes() == data
    parts = [call for call in store.calls if call[0] == "upload_part"]
    assert [call[3] for call in parts] == [5 * MIB, 5 * MIB, 2 * MIB + 5]
    assert [call[2] for call in parts] == [1, 2, 3]


def test_relay_of_a_file_smaller_than_one_part(tmp_path, monkeypatch):
    store = FakeR2()
    data = b"%PDF-1.7 small"
    _metadata, digest, partial = _relay(
        tmp_path, monkeypatch, store, StreamRequest([data]), len(data)
    )
    assert store.objects["upload_staging/k"] == data and partial.read_bytes() == data
    assert digest == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    ("label", "chunks_for", "declared"),
    [
        ("short", lambda data: _chunks(data)[:-3], None),
        ("long", lambda data: _chunks(data) + [b"extra"], None),
        ("disconnect", lambda data: _chunks(data), "disconnect"),
    ],
)
def test_relay_failures_abort_storage_and_leave_no_partial_file(
    tmp_path, monkeypatch, label, chunks_for, declared
):
    store = FakeR2()
    data = os.urandom(11 * MIB)
    request = StreamRequest(
        chunks_for(data), disconnect_after=100 if declared == "disconnect" else None
    )
    expected = ClientDisconnect if declared == "disconnect" else ConflictError
    with pytest.raises(expected):
        _relay(tmp_path, monkeypatch, store, request, len(data))
    assert store.uploads == {}
    assert ("abort_multipart", "upload_staging/k") in store.calls
    assert "upload_staging/k" not in store.objects
    assert list((tmp_path / "upload_staging").iterdir()) == []


class StallingRequest(StreamRequest):
    async def stream(self):
        yield self._chunks[0]
        await asyncio.sleep(30)
        yield self._chunks[1]


def test_a_stalled_relay_is_dropped_after_the_idle_deadline(tmp_path, monkeypatch):
    store = FakeR2()
    data = os.urandom(6 * MIB)
    request = StallingRequest(_chunks(data, size=3 * MIB))
    with pytest.raises(ConflictError, match="stopped sending data"):
        _relay(
            tmp_path, monkeypatch, store, request, len(data), direct_upload_relay_idle_seconds=0.3
        )
    assert store.uploads == {}
    assert ("abort_multipart", "upload_staging/k") in store.calls
    assert list((tmp_path / "upload_staging").iterdir()) == []


def test_relay_part_failure_surfaces_and_cleans_up(tmp_path, monkeypatch):
    store = FakeR2()
    store.fail_part = 2
    data = os.urandom(16 * MIB)
    with pytest.raises(OSError, match="part failure"):
        _relay(tmp_path, monkeypatch, store, StreamRequest(_chunks(data)), len(data))
    assert store.uploads == {}
    assert list((tmp_path / "upload_staging").iterdir()) == []


def test_relay_reading_waits_for_slow_storage(tmp_path, monkeypatch):
    """Backpressure: with storage slower than the client, the reader stops
    pulling the body instead of buffering the whole file in memory."""
    store = FakeR2(part_delay=0.15)
    data = os.urandom(30 * MIB)
    request = StreamRequest(_chunks(data))
    observed = []
    real_upload = store.upload_part

    def watching(key, upload_id, number, body):
        observed.append(request.consumed - number * 5 * MIB)
        return real_upload(key, upload_id, number, body)

    store.upload_part = watching
    _relay(tmp_path, monkeypatch, store, request, len(data), direct_upload_relay_queued_parts=1)
    # Never more than the stored part, one queued part and one filling part ahead.
    assert max(observed) <= 2 * 5 * MIB


# --- live API ----------------------------------------------------------------


@pytest.fixture(scope="module")
def scratch_url():
    from alembic import command
    from tests.test_schema import _admin_execute, _alembic_config, _swap_db

    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    url = _swap_db(base, SCRATCH_DB)
    command.upgrade(_alembic_config(url), "head")
    yield url
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))


def _seed_instructor(url, email):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.instructor import Instructor

    async def seed():
        engine = create_async_engine(sqlalchemy_url(url))
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                found = await session.scalar(select(Instructor).where(Instructor.email == email))
                if found is None:
                    found = Instructor(
                        email=email, display_name="Prof", password_hash=hash_password("s3cret!")
                    )
                    session.add(found)
                    await session.commit()
                return found.id
        finally:
            await engine.dispose()

    return asyncio.run(seed())


@pytest.fixture()
def wired(scratch_url, tmp_path, monkeypatch):
    import app.db as db
    import app.ingest.service as ingest_service
    import app.ingest.upload_worker as upload_worker
    import app.main as main
    import app.ratelimit as ratelimit

    monkeypatch.setenv("DATABASE_URL", scratch_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    monkeypatch.setenv("PIPELINE_WORKER_AUTOSTART", "false")
    monkeypatch.setenv("DIRECT_MANUSCRIPT_UPLOAD_ENABLED", "true")
    monkeypatch.setenv("STORAGE_BACKEND", "r2")
    monkeypatch.setenv("NATIVE_COMPUTE_URL", "https://native.invalid/api/native_extract")
    monkeypatch.setenv("NATIVE_COMPUTE_SECRET", "private-test-credential-" * 3)
    monkeypatch.setenv("STORAGE_TRANSFER_CHUNK_BYTES", str(5 * MIB))
    monkeypatch.setenv("DIRECT_UPLOAD_PART_MB", "5")
    get_settings.cache_clear()
    db._engine = None
    auth_service._rate_limiter = None
    ratelimit._limiters.clear()
    store = FakeR2()
    for module in (direct_upload, upload_worker, ingest_service, compute_client):
        monkeypatch.setattr(module, "get_storage", lambda _settings, **_: store)

    def helper(_url, *, content, headers, **_):
        # The native helper's own code, in process, against the same bucket.
        import httpx

        settings = get_settings()
        job = decode_job(content, headers[SIGNATURE_HEADER], settings)
        receipt = execute_native_job(job, settings, store)
        return httpx.Response(
            200,
            json={
                "receipt": receipt,
                "signature": signature(canonical_json(receipt), settings.native_compute_secret),
            },
        )

    monkeypatch.setattr(compute_client.httpx, "post", helper)

    async def idle_upload_worker():
        await asyncio.Event().wait()

    # Each test drives the worker itself.
    monkeypatch.setattr(main, "upload_worker_loop", idle_upload_worker)
    email = f"prof-{uuid4().hex[:8]}@tip.edu.ph"
    instructor_id = _seed_instructor(scratch_url, email)

    def client(transport):
        monkeypatch.setenv("DIRECT_UPLOAD_TRANSPORT", transport)
        get_settings.cache_clear()
        tc = TestClient(main.app)
        tc.__enter__()
        response = tc.post("/auth/login", json={"email": email, "password": "s3cret!"})
        assert response.status_code == 200, response.text
        opened.append(tc)
        return tc

    opened = []
    yield SimpleNamespace(
        client=client, store=store, instructor_id=instructor_id, db=db, scratch_url=scratch_url
    )
    for tc in opened:
        tc.__exit__(None, None, None)
    db._engine = None
    get_settings.cache_clear()


def _noise_pdf(megabytes):
    """A real PDF whose size comes from an incompressible embedded image."""
    side = int((megabytes * MIB / 3) ** 0.5)
    rng = random.Random(527)
    pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, side, side), False)
    pix.set_rect(pix.irect, (0, 0, 0))
    samples = bytearray(rng.getrandbits(8) for _ in range(side * side * 3))
    pix = pymupdf.Pixmap(pymupdf.csRGB, side, side, bytes(samples), False)
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 40), "Chapter 1 Introduction", fontsize=18)
        page.insert_text((40, 70), "A capstone manuscript with one large figure.")
        page.insert_image(pymupdf.Rect(40, 90, 560, 610), pixmap=pix)
        return doc.tobytes(deflate=False)


def _begin(tc, instructor_id, data, *, transports=None, attempt_id=None):
    headers = {"X-Veridical-Expected-Instructor": str(instructor_id)}
    if transports:
        headers[direct_upload.TRANSPORTS_HEADER] = transports
    response = tc.post(
        "/manuscripts/uploads",
        json={
            "attempt_id": attempt_id or str(uuid4()),
            "filename": "thesis.pdf",
            "size": len(data),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _process(wired):
    from app.ingest.upload_worker import process_next_upload

    async def run():
        wired.db._engine = None
        try:
            return await process_next_upload(get_settings())
        finally:
            await wired.db.get_engine().dispose()
            wired.db._engine = None

    return asyncio.run(run())


def _attempt(wired, attempt_id):
    from app.models.manuscript import Manuscript
    from app.models.upload_attempt import UploadAttempt

    async def load():
        wired.db._engine = None
        try:
            async with wired.db.get_session_factory()() as session:
                attempt = await session.get(UploadAttempt, attempt_id)
                manuscript = (
                    await session.get(Manuscript, attempt.manuscript_id)
                    if attempt.manuscript_id
                    else None
                )
                return attempt, manuscript
        finally:
            await wired.db.get_engine().dispose()
            wired.db._engine = None

    return asyncio.run(load())


@live
def test_relay_body_then_worker_moves_no_extra_bytes(wired, monkeypatch):
    tc = wired.client("relay")
    data = _noise_pdf(11)
    state = _begin(tc, wired.instructor_id, data, transports="stream")
    assert state["transport"] == "relay" and state["relay_mode"] == "stream"
    attempt_id = state["attempt_id"]
    response = tc.put(
        f"/manuscripts/uploads/{attempt_id}/body",
        content=data,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Veridical-Expected-Instructor": str(wired.instructor_id),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"
    local = direct_upload.relay_local_path(get_settings(), attempt_id)
    assert local.read_bytes() == data
    staging_key = next(call[1] for call in wired.store.calls if call[0] == "create_multipart")
    assert wired.store.objects[staging_key] == data

    from sqlalchemy import text

    import app.ingest.service as ingest_service

    seen_sessions, transaction_states = [], []
    real_client_for = ingest_service.get_llm_client_for

    async def recording_client_for(session, settings, instructor_id):
        # In fake mode the key lookup returns before touching the database;
        # read once here as the real lookup does, so a missing commit after
        # it would leave a transaction open and fail this test.
        await session.execute(text("SELECT 1"))
        seen_sessions.append(session)
        return await real_client_for(session, settings, instructor_id)

    real_read_images = ingest_service.vision.read_images

    async def checking_read_images(*args, **kwargs):
        transaction_states.append(("images", seen_sessions[-1].in_transaction()))
        return await real_read_images(*args, **kwargs)

    async def checking_read_figures(*args, **kwargs):
        transaction_states.append(("figures", seen_sessions[-1].in_transaction()))

    monkeypatch.setattr(ingest_service, "get_llm_client_for", recording_client_for)
    monkeypatch.setattr(ingest_service.vision, "read_images", checking_read_images)
    # Make the figure branch run too; its reading is replaced by the check.
    monkeypatch.setattr(ingest_service.figures, "prepare_figures", lambda *a, **k: [0])
    monkeypatch.setattr(ingest_service.figures, "read_figures", checking_read_figures)
    tc.__exit__(None, None, None)
    wired.store.calls.clear()

    assert _process(wired) is True
    attempt, manuscript = _attempt(wired, attempt_id)
    assert attempt.status == "done", attempt.error
    assert manuscript.content_hash == hashlib.sha256(data).hexdigest()
    durable = f"uploads/{manuscript.id}.pdf"
    assert wired.store.objects[durable] == data
    downloads = [call[1] for call in wired.store.calls if call[0] == "download_to"]
    assert ("copy", staging_key, durable, attempt.source_etag) in wired.store.calls
    # This host never downloads the staged file; only the helper reads the
    # durable copy, once. Nothing re-uploads the original.
    assert staging_key not in downloads
    assert downloads.count(durable) == 1
    assert ("put_file", durable) not in wired.store.calls
    assert not local.exists()
    # No transaction is held open while images or figures are read (the
    # database ends transactions left idle for 5 minutes).
    assert transaction_states == [("images", False), ("figures", False)]


@live
def test_worker_downloads_once_when_the_relay_copy_is_on_another_instance(wired):
    tc = wired.client("relay")
    data = _noise_pdf(6)
    attempt_id = _begin(tc, wired.instructor_id, data, transports="stream")["attempt_id"]
    response = tc.put(
        f"/manuscripts/uploads/{attempt_id}/body",
        content=data,
        headers={"X-Veridical-Expected-Instructor": str(wired.instructor_id)},
    )
    assert response.status_code == 200, response.text
    tc.__exit__(None, None, None)
    direct_upload.relay_local_path(get_settings(), attempt_id).unlink()
    wired.store.calls.clear()

    assert _process(wired) is True
    attempt, manuscript = _attempt(wired, attempt_id)
    assert attempt.status == "done", attempt.error
    assert manuscript.content_hash == hashlib.sha256(data).hexdigest()
    durable = f"uploads/{manuscript.id}.pdf"
    downloads = [call for call in wired.store.calls if call[0] == "download_to"]
    staged = [call for call in downloads if call[1] != durable and "native_compute" not in call[1]]
    assert len(staged) == 1 and staged[0][2] == attempt.source_etag
    assert [call[1] for call in downloads].count(durable) == 1
    assert ("put_file", durable) not in wired.store.calls


@live
def test_relay_body_refuses_a_wrong_length_before_reading(wired):
    tc = wired.client("relay")
    data = os.urandom(MIB)
    attempt_id = _begin(tc, wired.instructor_id, data, transports="stream")["attempt_id"]
    response = tc.put(f"/manuscripts/uploads/{attempt_id}/body", content=data + b"x")
    assert response.status_code == 409, response.text
    assert wired.store.calls == []


@live
def test_relay_body_accepts_a_chunked_body_and_holds_it_to_the_size(wired):
    tc = wired.client("relay")
    data = os.urandom(6 * MIB + 3)

    def chunked(payload):
        for offset in range(0, len(payload), 256 * 1024):
            yield payload[offset : offset + 256 * 1024]

    attempt_id = _begin(tc, wired.instructor_id, data, transports="stream")["attempt_id"]
    too_long = tc.put(f"/manuscripts/uploads/{attempt_id}/body", content=chunked(data + b"x"))
    assert too_long.status_code == 409, too_long.text
    assert wired.store.uploads == {}
    accepted = tc.put(f"/manuscripts/uploads/{attempt_id}/body", content=chunked(data))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "queued"
    staging_key = next(call[1] for call in wired.store.calls if call[0] == "complete_multipart")
    assert wired.store.objects[staging_key] == data


@live
def test_relay_body_requires_sign_in_before_any_byte_is_stored(wired):
    tc = wired.client("relay")
    data = os.urandom(MIB)
    attempt_id = _begin(tc, wired.instructor_id, data, transports="stream")["attempt_id"]
    tc.cookies.clear()
    response = tc.put(f"/manuscripts/uploads/{attempt_id}/body", content=data)
    assert response.status_code == 401, response.text
    assert wired.store.calls == []


@live
def test_relay_body_reports_busy_while_another_transfer_runs(wired):
    tc = wired.client("relay")
    data = os.urandom(MIB)
    attempt_id = _begin(tc, wired.instructor_id, data, transports="stream")["attempt_id"]
    assert direct_upload._relay_gate.acquire(blocking=False)
    try:
        response = tc.put(f"/manuscripts/uploads/{attempt_id}/body", content=data)
    finally:
        direct_upload._relay_gate.release()
    assert response.status_code == 503, response.text
    retry = tc.put(f"/manuscripts/uploads/{attempt_id}/body", content=data)
    assert retry.status_code == 200 and retry.json()["status"] == "queued"


@live
def test_older_clients_keep_the_multipart_form_relay(wired):
    tc = wired.client("relay")
    data = os.urandom(MIB)
    state = _begin(tc, wired.instructor_id, data)
    assert "relay_mode" not in state
    response = tc.post(
        f"/manuscripts/uploads/{state['attempt_id']}/file",
        files={"file": ("thesis.pdf", data, "application/pdf")},
    )
    assert response.status_code == 200 and response.json()["status"] == "queued"
    # The form relay also leaves this instance's copy for the worker.
    settings = get_settings()
    local = direct_upload.relay_local_path(settings, state["attempt_id"])
    assert local.read_bytes() == data
    assert source_digest.file_sha256(local, settings) == hashlib.sha256(data).hexdigest()
    assert [p.name for p in local.parent.iterdir()] == [local.name]


@live
def test_browser_parts_are_signed_resumed_and_completed_from_storage(wired):
    tc = wired.client("r2")
    data = os.urandom(12 * MIB + 9)
    attempt_id = str(uuid4())
    state = _begin(tc, wired.instructor_id, data, transports="parts,stream", attempt_id=attempt_id)
    assert "upload_url" not in state
    assert state["part_concurrency"] >= 1 and state["part_retries"] >= 0
    parts = state["parts"]
    assert [(p["number"], p["size"], p["done"]) for p in parts] == [
        (1, 5 * MIB, False),
        (2, 5 * MIB, False),
        (3, 2 * MIB + 9, False),
    ]
    assert all("partNumber=" in p["url"] for p in parts)
    (upload_id, upload) = next(iter(wired.store.uploads.items()))

    wired.store.put_part(upload["key"], upload_id, 1, data[: 5 * MIB])
    resumed = _begin(
        tc, wired.instructor_id, data, transports="parts,stream", attempt_id=attempt_id
    )
    assert [p["done"] for p in resumed["parts"]] == [True, False, False]
    assert "url" not in resumed["parts"][0]
    assert len(wired.store.uploads) == 1

    early = tc.post(f"/manuscripts/uploads/{attempt_id}/complete")
    assert early.status_code == 409, early.text
    wired.store.put_part(upload["key"], upload_id, 3, data[10 * MIB :])
    wired.store.put_part(upload["key"], upload_id, 2, data[5 * MIB : 10 * MIB])
    done = tc.post(f"/manuscripts/uploads/{attempt_id}/complete")
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "queued"
    assert wired.store.objects[upload["key"]] == data
    again = tc.post(f"/manuscripts/uploads/{attempt_id}/complete")
    assert again.status_code == 200 and again.json()["status"] == "queued"


@live
def test_a_lost_completion_reply_resumes_at_completion(wired):
    tc = wired.client("r2")
    data = os.urandom(11 * MIB)
    attempt_id = str(uuid4())
    _begin(tc, wired.instructor_id, data, transports="parts", attempt_id=attempt_id)
    (upload_id, upload) = next(iter(wired.store.uploads.items()))
    for number, (start, end) in enumerate(((0, 5 * MIB), (5 * MIB, 10 * MIB), (10 * MIB, None)), 1):
        wired.store.put_part(upload["key"], upload_id, number, data[start:end])
    # Storage assembled the file, but the reply never reached the browser.
    wired.store.complete_multipart(upload["key"], upload_id, [{"PartNumber": n} for n in (1, 2, 3)])
    resumed = _begin(tc, wired.instructor_id, data, transports="parts", attempt_id=attempt_id)
    assert [p["done"] for p in resumed["parts"]] == [True, True, True]
    assert all("url" not in p for p in resumed["parts"])
    done = tc.post(f"/manuscripts/uploads/{attempt_id}/complete")
    assert done.status_code == 200 and done.json()["status"] == "queued", done.text


@live
def test_part_size_is_fixed_when_the_attempt_begins(wired, monkeypatch):
    tc = wired.client("r2")
    data = os.urandom(11 * MIB)
    attempt_id = str(uuid4())
    first = _begin(tc, wired.instructor_id, data, transports="parts", attempt_id=attempt_id)
    monkeypatch.setenv("DIRECT_UPLOAD_PART_MB", "6")
    get_settings.cache_clear()
    again = _begin(tc, wired.instructor_id, data, transports="parts", attempt_id=attempt_id)
    assert [p["size"] for p in again["parts"]] == [p["size"] for p in first["parts"]]
    (upload_id, upload) = next(iter(wired.store.uploads.items()))
    for part in again["parts"]:
        start = (part["number"] - 1) * 5 * MIB
        wired.store.put_part(
            upload["key"], upload_id, part["number"], data[start : start + part["size"]]
        )
    done = tc.post(f"/manuscripts/uploads/{attempt_id}/complete")
    assert done.status_code == 200 and done.json()["status"] == "queued", done.text


@live
def test_choosing_the_same_file_again_replaces_the_older_attempt(wired, monkeypatch):
    monkeypatch.setenv("DIRECT_UPLOAD_PENDING_PER_INSTRUCTOR", "1")
    tc = wired.client("relay")
    data = os.urandom(MIB)
    older = _begin(tc, wired.instructor_id, data)["attempt_id"]
    newer = _begin(tc, wired.instructor_id, data)["attempt_id"]
    assert newer != older
    replaced, _ = _attempt(wired, older)
    assert replaced.status == "expired"
    assert replaced.error == "A newer upload of this file replaced this attempt."
    other_file = tc.post(
        "/manuscripts/uploads",
        json={"attempt_id": str(uuid4()), "filename": "other.pdf", "size": len(data)},
    )
    assert other_file.status_code == 429, other_file.text


@live
def test_wrong_sized_part_is_never_completed(wired):
    tc = wired.client("r2")
    data = os.urandom(11 * MIB)
    attempt_id = str(uuid4())
    _begin(tc, wired.instructor_id, data, transports="parts", attempt_id=attempt_id)
    (upload_id, upload) = next(iter(wired.store.uploads.items()))
    wired.store.put_part(upload["key"], upload_id, 1, data[: 5 * MIB])
    wired.store.put_part(upload["key"], upload_id, 2, data[5 * MIB : 10 * MIB - 1])
    wired.store.put_part(upload["key"], upload_id, 3, data[10 * MIB :])
    response = tc.post(f"/manuscripts/uploads/{attempt_id}/complete")
    assert response.status_code == 409, response.text
    assert upload["key"] not in wired.store.objects


@live
def test_clients_without_parts_keep_the_single_signed_upload(wired):
    tc = wired.client("r2")
    data = os.urandom(MIB)
    state = _begin(tc, wired.instructor_id, data)
    assert state["upload_url"] and "parts" not in state
    assert wired.store.uploads == {}


@live
def test_the_owner_ceiling_is_150_mb(wired):
    tc = wired.client("relay")
    assert get_settings().direct_manuscript_upload_max_mb == 150
    response = tc.post(
        "/manuscripts/uploads",
        json={"attempt_id": str(uuid4()), "filename": "thesis.pdf", "size": 150 * MIB + 1},
    )
    assert response.status_code == 413, response.text
    assert "150 MB" in response.json()["error"]["message"]
    accepted = tc.post(
        "/manuscripts/uploads",
        json={"attempt_id": str(uuid4()), "filename": "thesis.pdf", "size": 150 * MIB},
    )
    assert accepted.status_code == 200, accepted.text
