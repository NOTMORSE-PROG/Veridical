"""Large-source transfers stay bounded and publish only complete objects (F1)."""

import io
import tracemalloc
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from app.config import Settings
from app.storage import R2Storage, ensure_local_file


class GeneratedBody:
    """Generate transport bytes lazily; no preallocated manuscript-sized fixture."""

    def __init__(self, length, *, interrupt=False):
        self.remaining = length
        self.closed = False
        self.requests = []
        self.interrupt = interrupt

    def read(self, size):
        assert 0 < size <= 1_048_576
        self.requests.append(size)
        if self.interrupt and len(self.requests) > 1:
            raise TimeoutError("Controlled interrupted download")
        count = min(size, self.remaining)
        self.remaining -= count
        return b"x" * count

    def close(self):
        self.closed = True


def r2_with_body(body, length):
    class Client:
        def get_object(self, **kwargs):
            assert kwargs == {"Bucket": "private-test", "Key": "uploads/source.pdf"}
            return {"Body": body, "ContentLength": length}

    storage = R2Storage.__new__(R2Storage)
    storage._bucket, storage._client = "private-test", Client()
    return storage


def test_120_mib_cache_fill_has_bounded_python_memory_and_exact_bytes(tmp_path, monkeypatch):
    size = 120 * 1024 * 1024
    body = GeneratedBody(size)
    storage = r2_with_body(body, size)
    monkeypatch.setattr(storage, "get_bytes", lambda *_: pytest.fail("Unbounded source read"))
    settings = Settings(_env_file=None, data_dir=tmp_path)
    destination = tmp_path / "uploads" / "source.pdf"
    tracemalloc.start()
    try:
        assert ensure_local_file(settings, storage, str(destination)) == destination
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * settings.storage_download_chunk_bytes
    assert destination.stat().st_size == size
    with destination.open("rb") as reader:
        while block := reader.read(settings.storage_download_chunk_bytes):
            assert block == b"x" * len(block)
    assert body.closed and len(body.requests) == 121
    assert list(destination.parent.iterdir()) == [destination]


@pytest.mark.parametrize("failure", ["short", "long", "missing_length", "negative", "interrupt"])
def test_failed_stream_closes_transport_and_leaves_no_partial_cache(tmp_path, failure):
    length = 2 * 1_048_576
    body = GeneratedBody(length, interrupt=failure == "interrupt")
    expected = {
        "short": length + 1,
        "long": length - 1,
        "missing_length": None,
        "negative": -1,
        "interrupt": length,
    }[failure]
    storage = r2_with_body(body, expected)
    destination = tmp_path / "uploads" / "source.pdf"
    with pytest.raises((OSError, ValueError)):
        ensure_local_file(Settings(_env_file=None, data_dir=tmp_path), storage, str(destination))
    assert body.closed
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


@pytest.mark.parametrize(
    "code,status,expected",
    [("NoSuchKey", 404, FileNotFoundError), ("AccessDenied", 403, ClientError)],
)
def test_missing_and_denied_storage_are_distinct(code, status, expected):
    class Client:
        def get_object(self, **_):
            raise ClientError(
                {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
                "GetObject",
            )

    storage = R2Storage.__new__(R2Storage)
    storage._bucket, storage._client = "private-test", Client()
    with pytest.raises(expected):
        storage.download_to("uploads/source.pdf", io.BytesIO(), chunk_bytes=1_048_576)


def test_disk_write_failure_closes_remote_body():
    body = GeneratedBody(2 * 1_048_576)
    storage = r2_with_body(body, 2 * 1_048_576)

    class FullDisk:
        def write(self, _):
            raise OSError("Controlled disk full")

    with pytest.raises(OSError, match="Controlled disk full"):
        storage.download_to("uploads/source.pdf", FullDisk(), chunk_bytes=1_048_576)
    assert body.closed


def test_streamed_competing_fill_checks_through_eof_without_replacing_winner(tmp_path, monkeypatch):
    from app import storage as module

    settings = Settings(_env_file=None, data_dir=tmp_path, storage_download_chunk_bytes=8)
    destination = tmp_path / "uploads" / "source.pdf"
    original_publish = module.os.rename if module.os.name == "nt" else module.os.link
    first = True

    def competing_publish(source, target):
        nonlocal first
        if first:
            first = False
            Path(target).write_bytes(b"x" * 20 + b"different tail")
        return original_publish(source, target)

    monkeypatch.setattr(
        module.os, "rename" if module.os.name == "nt" else "link", competing_publish
    )
    with pytest.raises(FileExistsError):
        ensure_local_file(settings, r2_with_body(GeneratedBody(20), 20), str(destination))
    assert destination.read_bytes() == b"x" * 20 + b"different tail"
    assert list(destination.parent.iterdir()) == [destination]
