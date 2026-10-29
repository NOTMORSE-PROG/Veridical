"""F8: large source copies stay bounded and outlive exactly one response."""

import hashlib
import tracemalloc

import httpx
import pytest

from app.config import Settings
from app.errors import ConflictError, RateLimitedError
from app.report.router import SourceSnapshotResponse
from app.report.source_snapshot import SourceSnapshot


def test_large_copy_is_exact_isolated_and_bounded(tmp_path):
    path = tmp_path / "copy-stress.bin"
    digest = hashlib.sha256()
    chunk = b"bounded copy control" * 4096
    with path.open("wb") as writer:
        for _ in range(1400):
            writer.write(chunk)
            digest.update(chunk)
    assert path.stat().st_size > 100 * 1024 * 1024
    settings = Settings(_env_file=None, evidence_source_slots=1)
    tracemalloc.start()
    try:
        snapshot = SourceSnapshot(path, digest.hexdigest(), settings)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    try:
        assert peak < settings.storage_download_chunk_bytes * 8
        assert snapshot.size == path.stat().st_size
        with pytest.raises(RateLimitedError):
            SourceSnapshot(path, digest.hexdigest(), settings)
        path.write_bytes(b"changed original")
        with snapshot.path.open("rb") as reader:
            assert hashlib.file_digest(reader, "sha256").hexdigest() == digest.hexdigest()
    finally:
        snapshot.close()
        snapshot.close()
    assert not snapshot.path.exists()
    with pytest.raises(ConflictError):
        SourceSnapshot(path, digest.hexdigest(), settings)
    with SourceSnapshot(path, hashlib.sha256(path.read_bytes()).hexdigest(), settings):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "range_header,expected",
    [(None, 200), ("bytes=1-3", 206), ("nonsense", 400), ("bytes=999-", 416)],
)
async def test_stream_cleanup_includes_range_rejections(tmp_path, range_header, expected):
    path = tmp_path / "source-control.bin"
    data = b"exact source response control"
    path.write_bytes(data)
    snapshot = SourceSnapshot(path, hashlib.sha256(data).hexdigest(), Settings(_env_file=None))
    response = SourceSnapshotResponse(snapshot, "a" * 64)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=response), base_url="http://test"
    ) as client:
        result = await client.get("/", headers={"Range": range_header} if range_header else {})
    assert result.status_code == expected
    assert result.headers.get("cache-control") == "private, no-store" or expected in (400, 416)
    assert not snapshot.path.exists()
