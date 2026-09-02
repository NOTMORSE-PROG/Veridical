"""BUG-138 regression: a manuscript's source file and derived extraction
JSON must survive Render's ephemeral disk being wiped (a redeploy, an
idle-wake) — not just live as long as the local cache does.

`FakeStorage` below stands in for a real Cloudflare R2 bucket: it's the same
`Storage` contract (`app/storage/__init__.py`), so exercising it against
`ensure_local_file`/`load_raw_store`/`manuscript_file_path_for` proves the
recovery mechanism itself, independent of any live R2 credentials (none are
provisioned for this dev/test environment — `storage_backend` stays "local"
by default; this test only fakes what "r2" would do).
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.archive.service as archive_service
import app.auth.service as auth_service
import app.ingest.service as ingest_service
import app.report.service as report_service
from app.auth.security import hash_password
from app.config import get_settings
from app.storage import Storage, ensure_local_file, get_storage, storage_key_for
from tests.test_ingest_fixtures import FIXTURE_DIR

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)

SCRATCH_DB = "veridical_bug138test"


class FakeStorage:
    """In-memory stand-in for `R2Storage`, same `Storage` contract."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_file(self, local_path: Path, key: str) -> None:
        self.objects[key] = local_path.read_bytes()

    def get_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


def test_ensure_local_file_recovers_after_the_local_cache_is_wiped(tmp_path):
    """The core mechanism, isolated from the DB/HTTP stack: once durable
    storage has a copy, deleting the local cache file is not data loss."""
    from app.config import Settings

    settings = Settings(data_dir=tmp_path)
    local_path = tmp_path / "uploads" / "1.pdf"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"%PDF-1.4 fake content")

    storage: Storage = FakeStorage()
    storage.put_file(local_path, storage_key_for(settings, str(local_path)))

    # Simulate Render's ephemeral-disk wipe: the file that existed a moment
    # ago is just gone, the way it is after every redeploy/idle-wake.
    local_path.unlink()
    assert not local_path.exists()

    recovered = ensure_local_file(settings, storage, str(local_path))
    assert recovered == local_path
    assert recovered.read_bytes() == b"%PDF-1.4 fake content"


def test_ensure_local_file_still_raises_when_truly_gone(tmp_path):
    """A file durable storage never had either (pre-migration row, real
    data loss) is an honest FileNotFoundError, not a silent empty read."""
    from app.config import Settings

    settings = Settings(data_dir=tmp_path)
    missing = tmp_path / "uploads" / "404.pdf"
    with pytest.raises(FileNotFoundError):
        ensure_local_file(settings, FakeStorage(), str(missing))


def test_get_storage_refuses_r2_with_incomplete_credentials(tmp_path):
    """Ground rule 7: no secrets defaulted/guessed. `STORAGE_BACKEND=r2`
    without all four R2 values must fail loudly at the point of use, not
    silently fall back to local (which would look like durability while
    providing none)."""
    from app.config import Settings

    settings = Settings(data_dir=tmp_path, storage_backend="r2", r2_bucket="veridical-uploads")
    with pytest.raises(RuntimeError, match="R2"):
        get_storage(settings)


@pytest.fixture(scope="module")
def api_scratch_url():
    import asyncio

    from alembic import command
    from tests.test_schema import _admin_execute, _alembic_config, _swap_db

    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    url = _swap_db(base, SCRATCH_DB)
    command.upgrade(_alembic_config(url), "head")
    yield url
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))


@pytest.fixture()
def client(api_scratch_url, tmp_path, monkeypatch):
    """App wired to the scratch DB and a temp data dir, with EVERY module
    that reads durable storage patched to share one `FakeStorage` — the
    same shape a real `STORAGE_BACKEND=r2` deployment has (one bucket, many
    callers), without needing live R2 credentials."""
    import asyncio

    import app.db as db
    import app.ratelimit as ratelimit
    from app.db import sqlalchemy_url
    from app.models.instructor import Instructor

    monkeypatch.setenv("DATABASE_URL", api_scratch_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    get_settings.cache_clear()
    db._engine = None
    auth_service._rate_limiter = None
    ratelimit._limiters.clear()

    shared_storage = FakeStorage()
    monkeypatch.setattr(ingest_service, "get_storage", lambda settings: shared_storage)
    monkeypatch.setattr(report_service, "get_storage", lambda settings: shared_storage)
    monkeypatch.setattr(archive_service, "get_storage", lambda settings: shared_storage)

    from app.main import app

    async def seed_instructor():
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                existing = await session.scalar(
                    select(Instructor).where(Instructor.email == "prof@tip.edu.ph")
                )
                if existing is None:
                    session.add(
                        Instructor(
                            email="prof@tip.edu.ph",
                            display_name="Prof",
                            password_hash=hash_password("s3cret!"),
                        )
                    )
                    await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed_instructor())

    with TestClient(app) as tc:
        tc.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
        yield tc, shared_storage
    db._engine = None
    get_settings.cache_clear()


@live
def test_manuscript_file_survives_a_simulated_ephemeral_disk_wipe(client):
    """The ticket's own measured symptom, reproduced: `GET .../document/file`
    500s on every completed run once the local disk is wiped, because
    nothing durable ever backed it up. With durable storage wired in, the
    SAME request after the SAME wipe succeeds."""
    tc, storage = client
    content = (FIXTURE_DIR / "native.pdf").read_bytes()
    r = tc.post(
        "/manuscripts/ingest",
        files={"file": ("native.pdf", content, "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    manuscript_id = r.json()["manuscript_id"]

    # Prove durable storage actually received both the upload and the
    # extraction JSON before wiping anything.
    assert any(k.endswith(".pdf") for k in storage.objects)
    assert any(k.endswith(".extraction.json") for k in storage.objects)

    settings = get_settings()
    data_dir = settings.data_dir
    for p in data_dir.rglob("*"):
        if p.is_file():
            p.unlink()

    # The library surface's own-manuscript document route needs no check
    # run and no rubric — the most direct path to `manuscript_file_path_for`.
    r = tc.get(f"/library/{manuscript_id}/document/file")
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF")


@live
def test_extraction_cache_survives_a_simulated_ephemeral_disk_wipe(client):
    """The ticket's second, resumability symptom: a structural check
    reading back the extraction JSON (`load_raw_store`, shared by the
    pipeline worker) must recover from durable storage instead of dying
    with a raw FileNotFoundError."""
    tc, storage = client
    content = (FIXTURE_DIR / "native.pdf").read_bytes()
    r = tc.post(
        "/manuscripts/ingest",
        files={"file": ("native.pdf", content, "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    manuscript_id = r.json()["manuscript_id"]

    settings = get_settings()
    for p in settings.data_dir.rglob("*"):
        if p.is_file():
            p.unlink()

    result = ingest_service.load_raw_store(settings, manuscript_id)
    assert result.blocks


@live
def test_purge_deletes_the_durable_copy_too(client):
    """`backend-critic` finding (BUG-138 review, DoD gap): purge must
    remove the DURABLE copy, or "purged" is a lie once R2 exists -- the
    bytes would outlive their own deletion forever instead of just until
    the next ephemeral-disk wipe. Exercises the real `DELETE /archive/{id}`
    route, not just the service function directly."""
    tc, storage = client
    content = (FIXTURE_DIR / "native.pdf").read_bytes()
    r = tc.post(
        "/manuscripts/ingest",
        files={"file": ("native.pdf", content, "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    manuscript_id = r.json()["manuscript_id"]

    upload_keys = [k for k in storage.objects if k.endswith(".pdf")]
    extraction_keys = [k for k in storage.objects if k.endswith(".extraction.json")]
    assert upload_keys and extraction_keys, "fixture assumption: durable write happened"

    r = tc.delete(f"/archive/{manuscript_id}")
    assert r.status_code == 200, r.text

    for key in upload_keys + extraction_keys:
        assert key not in storage.objects, f"{key} survived purge in durable storage"
