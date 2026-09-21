"""BUG-235: bounded, offline recovery of legacy content hashes.

These are live PostgreSQL tests because the compare-and-set update and stable
cursor semantics are the feature, not incidental implementation details.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.db import sqlalchemy_url
from app.maintenance.content_hashes import (
    ContentHashBackfillOutcome,
    _store_hash_if_still_eligible,
    backfill_content_hash_batch,
)
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_hashbackfilltest"


@pytest.fixture(scope="module")
def scratch_url():
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
def session_factory(scratch_url):
    engine = create_async_engine(sqlalchemy_url(scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean(session_factory):
    async with session_factory() as session:
        await session.execute(text("TRUNCATE manuscript, instructor RESTART IDENTITY CASCADE"))
        await session.commit()
    yield


class FakeStorage:
    def __init__(
        self,
        objects: dict[str, bytes] | None = None,
        *,
        failure_keys: set[str] | None = None,
    ) -> None:
        self.objects = objects or {}
        self.failure_keys = failure_keys or set()
        self.read_keys: list[str] = []

    def put_file(self, local_path: Path, key: str) -> None:
        del local_path, key

    def get_bytes(self, key: str) -> bytes:
        self.read_keys.append(key)
        if key in self.failure_keys:
            raise RuntimeError("secret-token=must-not-escape C:/private/source.pdf")
        try:
            return self.objects[key]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class BlockingReadStorage(FakeStorage):
    def __init__(self, objects: dict[str, bytes]) -> None:
        super().__init__(objects)
        self.read_started = threading.Event()
        self.resume_read = threading.Event()

    def get_bytes(self, key: str) -> bytes:
        self.read_keys.append(key)
        data = self.objects[key]
        self.read_started.set()
        if not self.resume_read.wait(timeout=5):
            raise RuntimeError("coordinated storage read timed out")
        return data


class BlockingMissingStorage(FakeStorage):
    def __init__(self) -> None:
        super().__init__()
        self.read_started = threading.Event()
        self.resume_read = threading.Event()

    def get_bytes(self, key: str) -> bytes:
        self.read_keys.append(key)
        self.read_started.set()
        if not self.resume_read.wait(timeout=5):
            raise RuntimeError("coordinated missing read timed out")
        raise FileNotFoundError(key)


async def _seed_manuscripts(
    session_factory,
    rows: list[dict[str, object]],
) -> list[int]:
    async with session_factory() as session:
        instructor = Instructor(email="legacy@test.local", display_name="Legacy Test")
        session.add(instructor)
        await session.flush()
        manuscripts = [
            Manuscript(
                instructor_id=instructor.id,
                group_label="Legacy",
                original_filename=str(row.get("filename", "private-name.pdf")),
                file_ref=str(row["file_ref"]),
                content_hash=row.get("content_hash"),
                purged_at=row.get("purged_at"),
            )
            for row in rows
        ]
        session.add_all(manuscripts)
        await session.commit()
        return [manuscript.id for manuscript in manuscripts]


async def _hashes_by_id(session_factory) -> dict[int, str | None]:
    async with session_factory() as session:
        rows = (await session.execute(select(Manuscript.id, Manuscript.content_hash))).all()
        return {row.id: row.content_hash for row in rows}


async def test_apply_recovers_local_and_durable_sources_in_a_bounded_resumable_batch(
    session_factory, tmp_path
):
    data_dir = tmp_path / "data"
    uploads = data_dir / "uploads"
    uploads.mkdir(parents=True)
    local_bytes = b"local legacy source"
    durable_bytes = b"durable legacy source"
    later_bytes = b"later eligible source"
    local_path = uploads / "local.pdf"
    durable_path = uploads / "durable.pdf"
    missing_path = uploads / "missing.pdf"
    later_path = uploads / "later.pdf"
    filled_path = uploads / "filled.pdf"
    purged_path = uploads / "purged.pdf"
    local_path.write_bytes(local_bytes)
    later_path.write_bytes(later_bytes)
    filled_path.write_bytes(b"already filled")
    purged_path.write_bytes(b"purged")
    existing_digest = hashlib.sha256(b"already filled").hexdigest()
    ids = await _seed_manuscripts(
        session_factory,
        [
            {"file_ref": local_path},
            {"file_ref": durable_path},
            {"file_ref": missing_path},
            {"file_ref": later_path},
            {"file_ref": filled_path, "content_hash": existing_digest},
            {"file_ref": purged_path, "purged_at": datetime.now(UTC)},
        ],
    )
    settings = Settings(
        _env_file=None,
        data_dir=data_dir,
        content_hash_backfill_batch_size=3,
        content_hash_read_chunk_bytes=4,
    )
    storage = FakeStorage({"uploads/durable.pdf": durable_bytes})

    async with session_factory() as session:
        first = await backfill_content_hash_batch(
            session,
            settings,
            through_id=ids[-1],
            apply=True,
            storage=storage,
        )

    assert [item.manuscript_id for item in first.items] == ids[:3]
    assert [item.outcome for item in first.items] == [
        ContentHashBackfillOutcome.updated,
        ContentHashBackfillOutcome.updated,
        ContentHashBackfillOutcome.missing_object,
    ]
    assert first.next_after_id == ids[2]
    assert first.retry_after_id == 0
    assert first.more_eligible is True
    assert not durable_path.exists()

    hashes = await _hashes_by_id(session_factory)
    assert hashes[ids[0]] == hashlib.sha256(local_bytes).hexdigest()
    assert hashes[ids[1]] == hashlib.sha256(durable_bytes).hexdigest()
    assert hashes[ids[2]] is None
    assert hashes[ids[3]] is None
    assert hashes[ids[4]] == existing_digest
    assert hashes[ids[5]] is None

    async with session_factory() as session:
        second = await backfill_content_hash_batch(
            session,
            settings,
            through_id=ids[-1],
            after_id=first.next_after_id,
            apply=True,
            storage=storage,
        )

    assert [(item.manuscript_id, item.outcome) for item in second.items] == [
        (ids[3], ContentHashBackfillOutcome.updated)
    ]
    assert second.more_eligible is False
    assert second.next_after_id == ids[3]
    assert second.retry_after_id is None

    # Idempotent rerun: recovered and excluded rows are not read or rewritten;
    # only the genuinely missing source remains eligible.
    storage.read_keys.clear()
    async with session_factory() as session:
        rerun = await backfill_content_hash_batch(
            session,
            settings,
            through_id=ids[-1],
            batch_size=10,
            apply=True,
            storage=storage,
        )
    assert [(item.manuscript_id, item.outcome) for item in rerun.items] == [
        (ids[2], ContentHashBackfillOutcome.missing_object)
    ]
    assert storage.read_keys == ["uploads/missing.pdf"]


async def test_preview_and_cursor_bounds_do_not_mutate_rows(session_factory, tmp_path):
    data_dir = tmp_path / "data"
    uploads = data_dir / "uploads"
    uploads.mkdir(parents=True)
    paths = [uploads / f"source-{index}.pdf" for index in range(4)]
    for index, path in enumerate(paths):
        path.write_bytes(f"source {index}".encode())
    ids = await _seed_manuscripts(session_factory, [{"file_ref": path} for path in paths])
    settings = Settings(_env_file=None, data_dir=data_dir)

    async with session_factory() as session:
        result = await backfill_content_hash_batch(
            session,
            settings,
            after_id=ids[0],
            through_id=ids[2],
            batch_size=1,
            apply=False,
            storage=FakeStorage(),
        )

    assert [(item.manuscript_id, item.outcome) for item in result.items] == [
        (ids[1], ContentHashBackfillOutcome.would_update)
    ]
    assert result.more_eligible is True
    assert result.next_after_id == ids[1]
    assert all(value is None for value in (await _hashes_by_id(session_factory)).values())

    async with session_factory() as session:
        with pytest.raises(ValueError, match="content_hash_backfill_max_batch_size"):
            await backfill_content_hash_batch(
                session,
                settings,
                through_id=ids[-1],
                batch_size=settings.content_hash_backfill_max_batch_size + 1,
                storage=FakeStorage(),
            )


async def test_compare_and_set_reports_concurrent_state_without_overwriting(
    session_factory, tmp_path
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    paths = [data_dir / f"race-{index}.pdf" for index in range(3)]
    ids = await _seed_manuscripts(session_factory, [{"file_ref": path} for path in paths])
    concurrent_digest = hashlib.sha256(b"concurrent").hexdigest()

    async with session_factory() as concurrent:
        await concurrent.execute(
            update(Manuscript).where(Manuscript.id == ids[0]).values(content_hash=concurrent_digest)
        )
        await concurrent.execute(
            update(Manuscript).where(Manuscript.id == ids[1]).values(purged_at=datetime.now(UTC))
        )
        await concurrent.execute(delete(Manuscript).where(Manuscript.id == ids[2]))
        await concurrent.commit()

    async with session_factory() as session:
        assert (
            await _store_hash_if_still_eligible(session, ids[0], concurrent_digest)
            == ContentHashBackfillOutcome.already_filled
        )
        assert (
            await _store_hash_if_still_eligible(session, ids[1], "1" * 64)
            == ContentHashBackfillOutcome.purged
        )
        assert (
            await _store_hash_if_still_eligible(session, ids[2], "2" * 64)
            == ContentHashBackfillOutcome.row_missing
        )

    async with session_factory() as session:
        assert (
            await _store_hash_if_still_eligible(session, ids[0], "0" * 64)
            == ContentHashBackfillOutcome.hash_conflict
        )

    hashes = await _hashes_by_id(session_factory)
    assert hashes[ids[0]] == concurrent_digest
    assert hashes[ids[1]] is None
    assert ids[2] not in hashes


async def test_storage_failure_is_fixed_shape_and_leaves_hash_null(session_factory, tmp_path):
    data_dir = tmp_path / "data"
    path = data_dir / "uploads" / "sensitive-name.pdf"
    ids = await _seed_manuscripts(session_factory, [{"file_ref": path}])
    settings = Settings(_env_file=None, data_dir=data_dir)
    storage = FakeStorage(failure_keys={"uploads/sensitive-name.pdf"})

    async with session_factory() as session:
        result = await backfill_content_hash_batch(
            session,
            settings,
            through_id=ids[0],
            apply=True,
            storage=storage,
        )

    assert result.items[0].outcome == ContentHashBackfillOutcome.storage_failure
    assert result.has_unrecovered is True
    assert (await _hashes_by_id(session_factory))[ids[0]] is None
    assert "secret-token" not in repr(result)
    assert "sensitive-name" not in repr(result)


async def test_database_failure_preserves_prior_outcomes_and_retries_failed_row(
    session_factory, tmp_path, monkeypatch
):
    from app.maintenance import content_hashes as service

    data_dir = tmp_path / "data"
    uploads = data_dir / "uploads"
    uploads.mkdir(parents=True)
    paths = [uploads / f"source-{index}.pdf" for index in range(2)]
    for index, path in enumerate(paths):
        path.write_bytes(f"source {index}".encode())
    ids = await _seed_manuscripts(session_factory, [{"file_ref": path} for path in paths])
    settings = Settings(_env_file=None, data_dir=data_dir)
    real_store = service._store_hash_if_still_eligible
    calls = 0

    async def fail_second_store(session, manuscript_id, digest):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("password=must-not-escape postgresql://private")
        return await real_store(session, manuscript_id, digest)

    monkeypatch.setattr(service, "_store_hash_if_still_eligible", fail_second_store)
    async with session_factory() as session:
        result = await service.backfill_content_hash_batch(
            session,
            settings,
            through_id=ids[-1],
            batch_size=2,
            apply=True,
            storage=FakeStorage(),
        )

    assert [(item.manuscript_id, item.outcome) for item in result.items] == [
        (ids[0], ContentHashBackfillOutcome.updated),
        (ids[1], ContentHashBackfillOutcome.database_failure),
    ]
    assert result.next_after_id == ids[0]
    assert result.more_eligible is True
    assert result.has_unrecovered is True
    assert "password" not in repr(result)
    hashes = await _hashes_by_id(session_factory)
    assert hashes[ids[0]] is not None
    assert hashes[ids[1]] is None

    # The emitted cursor deliberately remains before the ambiguous row. A
    # retry re-reads it instead of hiding it behind the continuation cursor.
    monkeypatch.setattr(service, "_store_hash_if_still_eligible", real_store)
    async with session_factory() as session:
        retry = await service.backfill_content_hash_batch(
            session,
            settings,
            through_id=ids[-1],
            after_id=result.next_after_id,
            batch_size=2,
            apply=True,
            storage=FakeStorage(),
        )
    assert [(item.manuscript_id, item.outcome) for item in retry.items] == [
        (ids[1], ContentHashBackfillOutcome.updated)
    ]
    assert retry.more_eligible is False


@pytest.mark.parametrize("apply", (False, True))
async def test_durable_recovery_racing_purge_rechecks_state_and_removes_cache(
    session_factory, tmp_path, apply
):
    data_dir = tmp_path / "data"
    path = data_dir / "uploads" / "race.pdf"
    ids = await _seed_manuscripts(session_factory, [{"file_ref": path}])
    settings = Settings(_env_file=None, data_dir=data_dir)
    storage = BlockingReadStorage({"uploads/race.pdf": b"durable bytes captured before purge"})

    async with session_factory() as session:
        task = asyncio.create_task(
            backfill_content_hash_batch(
                session,
                settings,
                through_id=ids[0],
                apply=apply,
                storage=storage,
            )
        )
        assert await asyncio.to_thread(storage.read_started.wait, 5)
        async with session_factory() as concurrent:
            await concurrent.execute(
                update(Manuscript)
                .where(Manuscript.id == ids[0])
                .values(purged_at=datetime.now(UTC))
            )
            await concurrent.commit()
        storage.delete("uploads/race.pdf")
        storage.resume_read.set()
        result = await task

    assert [(item.manuscript_id, item.outcome) for item in result.items] == [
        (ids[0], ContentHashBackfillOutcome.purged)
    ]
    assert not path.exists()
    assert (await _hashes_by_id(session_factory))[ids[0]] is None


async def test_real_purge_holds_shared_lock_through_delete_and_commit(
    session_factory, tmp_path, monkeypatch
):
    from app.archive import service as archive_service

    data_dir = tmp_path / "data"
    path = data_dir / "uploads" / "purge-race.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"local copy purge must remove")
    ids = await _seed_manuscripts(session_factory, [{"file_ref": path}])
    settings = Settings(_env_file=None, data_dir=data_dir)
    storage = FakeStorage({"uploads/purge-race.pdf": b"durable copy purge must remove"})
    purge_before_commit = asyncio.Event()
    allow_purge_commit = asyncio.Event()

    async def hold_audit_before_commit(*args, **kwargs):
        del args, kwargs
        purge_before_commit.set()
        await allow_purge_commit.wait()

    monkeypatch.setattr(archive_service, "get_storage", lambda _settings: storage)
    monkeypatch.setattr(archive_service, "write_audit_event", hold_audit_before_commit)

    async with (
        session_factory() as purge_session,
        session_factory() as recovery_session,
    ):
        purge_task = asyncio.create_task(
            archive_service.purge_manuscript(
                purge_session,
                instructor_id=1,
                manuscript_id=ids[0],
                settings=settings,
            )
        )
        await asyncio.wait_for(purge_before_commit.wait(), timeout=5)
        recovery_task = asyncio.create_task(
            backfill_content_hash_batch(
                recovery_session,
                settings,
                through_id=ids[0],
                apply=True,
                storage=storage,
            )
        )
        await asyncio.sleep(0.05)
        assert not recovery_task.done()
        allow_purge_commit.set()
        _, recovery = await asyncio.gather(purge_task, recovery_task)

    assert [(item.manuscript_id, item.outcome) for item in recovery.items] == [
        (ids[0], ContentHashBackfillOutcome.purged)
    ]
    assert storage.read_keys == []
    assert "uploads/purge-race.pdf" not in storage.objects
    assert not path.exists()
    async with session_factory() as session:
        row = await session.get(Manuscript, ids[0])
        assert row is not None
        assert row.purged_at is not None
        assert row.content_hash is None


async def test_cold_durable_preview_leaves_no_cache_or_database_mutation(session_factory, tmp_path):
    data_dir = tmp_path / "data"
    path = data_dir / "uploads" / "preview.pdf"
    ids = await _seed_manuscripts(session_factory, [{"file_ref": path}])
    settings = Settings(_env_file=None, data_dir=data_dir)
    storage = FakeStorage({"uploads/preview.pdf": b"preview-only durable bytes"})

    async with session_factory() as session:
        result = await backfill_content_hash_batch(
            session,
            settings,
            through_id=ids[0],
            apply=False,
            storage=storage,
        )

    assert [(item.manuscript_id, item.outcome) for item in result.items] == [
        (ids[0], ContentHashBackfillOutcome.would_update)
    ]
    assert not path.exists()
    assert (await _hashes_by_id(session_factory))[ids[0]] is None


@pytest.mark.parametrize(
    ("concurrent_change", "expected"),
    (
        ("purge", ContentHashBackfillOutcome.purged),
        ("delete", ContentHashBackfillOutcome.row_missing),
    ),
)
async def test_missing_recovery_reclassifies_concurrent_purge_or_delete(
    session_factory, tmp_path, concurrent_change, expected
):
    data_dir = tmp_path / "data"
    path = data_dir / "uploads" / "missing-race.pdf"
    ids = await _seed_manuscripts(session_factory, [{"file_ref": path}])
    settings = Settings(_env_file=None, data_dir=data_dir)
    storage = BlockingMissingStorage()

    async with session_factory() as session:
        task = asyncio.create_task(
            backfill_content_hash_batch(
                session,
                settings,
                through_id=ids[0],
                apply=True,
                storage=storage,
            )
        )
        assert await asyncio.to_thread(storage.read_started.wait, 5)
        async with session_factory() as concurrent:
            if concurrent_change == "purge":
                await concurrent.execute(
                    update(Manuscript)
                    .where(Manuscript.id == ids[0])
                    .values(purged_at=datetime.now(UTC))
                )
            else:
                await concurrent.execute(delete(Manuscript).where(Manuscript.id == ids[0]))
            await concurrent.commit()
        storage.resume_read.set()
        result = await task

    assert [(item.manuscript_id, item.outcome) for item in result.items] == [(ids[0], expected)]
    assert not path.exists()


async def test_two_workers_serialize_cold_durable_recovery(session_factory, tmp_path):
    data_dir = tmp_path / "data"
    path = data_dir / "uploads" / "same-row.pdf"
    content = b"one immutable source shared by two recovery workers"
    ids = await _seed_manuscripts(session_factory, [{"file_ref": path}])
    settings = Settings(_env_file=None, data_dir=data_dir)
    storage = BlockingReadStorage({"uploads/same-row.pdf": content})
    async with session_factory() as first_session, session_factory() as second_session:
        first_task = asyncio.create_task(
            backfill_content_hash_batch(
                first_session,
                settings,
                through_id=ids[0],
                apply=True,
                storage=storage,
            )
        )
        assert await asyncio.to_thread(storage.read_started.wait, 5)
        second_task = asyncio.create_task(
            backfill_content_hash_batch(
                second_session,
                settings,
                through_id=ids[0],
                apply=True,
                storage=storage,
            )
        )
        await asyncio.sleep(0.05)
        assert not second_task.done()
        storage.resume_read.set()
        first, second = await asyncio.gather(first_task, second_task)

    assert sorted((first.items[0].outcome, second.items[0].outcome)) == [
        ContentHashBackfillOutcome.already_filled,
        ContentHashBackfillOutcome.updated,
    ]
    assert storage.read_keys == ["uploads/same-row.pdf"]
    assert not path.exists()
    assert (await _hashes_by_id(session_factory))[ids[0]] == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize("path_shape", ("absolute", "traversal"))
async def test_out_of_root_legacy_source_is_rejected_without_reading(
    session_factory, tmp_path, path_shape
):
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside" / "same-name.pdf"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"must not be hashed")
    file_ref = outside if path_shape == "absolute" else data_dir / ".." / "outside" / outside.name
    ids = await _seed_manuscripts(session_factory, [{"file_ref": file_ref}])
    settings = Settings(_env_file=None, data_dir=data_dir)
    storage = FakeStorage({"same-name.pdf": b"wrong colliding durable object"})

    async with session_factory() as session:
        result = await backfill_content_hash_batch(
            session,
            settings,
            through_id=ids[0],
            apply=True,
            storage=storage,
        )

    assert [(item.manuscript_id, item.outcome) for item in result.items] == [
        (ids[0], ContentHashBackfillOutcome.invalid_source_ref)
    ]
    assert storage.read_keys == []
    assert outside.read_bytes() == b"must not be hashed"
    assert (await _hashes_by_id(session_factory))[ids[0]] is None


async def test_symlink_escape_legacy_source_is_rejected(session_factory, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.pdf"
    secret.write_bytes(b"must not be hashed through a symlink")
    link = data_dir / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink is unavailable: {type(exc).__name__}")
    ids = await _seed_manuscripts(session_factory, [{"file_ref": link / secret.name}])
    settings = Settings(_env_file=None, data_dir=data_dir)
    storage = FakeStorage()

    async with session_factory() as session:
        result = await backfill_content_hash_batch(
            session,
            settings,
            through_id=ids[0],
            apply=True,
            storage=storage,
        )

    assert result.items[0].outcome == ContentHashBackfillOutcome.invalid_source_ref
    assert storage.read_keys == []
    assert (await _hashes_by_id(session_factory))[ids[0]] is None


async def test_cli_output_and_exit_codes_are_bounded_and_sanitized(
    session_factory, scratch_url, tmp_path, monkeypatch, capsys
):
    from scripts import backfill_content_hashes as command

    data_dir = tmp_path / "data"
    uploads = data_dir / "uploads"
    uploads.mkdir(parents=True)
    present_path = uploads / "private-filename.pdf"
    missing_path = uploads / "missing-private-filename.pdf"
    content = b"private manuscript content must not be printed"
    present_path.write_bytes(content)
    ids = await _seed_manuscripts(
        session_factory,
        [{"file_ref": present_path}, {"file_ref": missing_path}],
    )
    settings = Settings(
        _env_file=None,
        database_url=scratch_url,
        data_dir=data_dir,
        storage_backend="local",
    )
    monkeypatch.setattr(command, "get_settings", lambda: settings)

    exit_code = await command.run(
        through_id=ids[-1],
        after_id=0,
        batch_size=2,
        apply=False,
    )
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 1
    assert payload["status"] == "partial"
    assert payload["items"] == [
        {"manuscript_id": ids[0], "outcome": "would_update"},
        {"manuscript_id": ids[1], "outcome": "missing_object"},
    ]
    assert "private-filename" not in output
    assert str(data_dir) not in output
    assert content.decode() not in output
    assert hashlib.sha256(content).hexdigest() not in output

    fatal_code = await command.run(
        through_id=ids[0],
        after_id=ids[0],
        batch_size=1,
        apply=True,
    )
    fatal_output = capsys.readouterr().out
    assert fatal_code == 2
    assert json.loads(fatal_output) == {
        "status": "fatal",
        "code": "database_or_command_failure",
        "message": "Backfill could not produce a trustworthy batch result.",
    }
    assert scratch_url not in fatal_output

    real_dispose = command._dispose_engine

    async def dispose_then_fail(engine):
        await real_dispose(engine)
        raise RuntimeError("secret cleanup endpoint must not escape")

    monkeypatch.setattr(command, "_dispose_engine", dispose_then_fail)
    cleanup_code = await command.run(
        through_id=ids[-1],
        after_id=0,
        batch_size=1,
        apply=True,
    )
    cleanup_output = capsys.readouterr().out
    cleanup_payload = json.loads(cleanup_output)
    assert cleanup_code == 1
    assert cleanup_payload["status"] == "partial"
    assert cleanup_payload["post_result_failure"] is True
    assert cleanup_payload["items"] == [{"manuscript_id": ids[0], "outcome": "updated"}]
    assert "secret cleanup" not in cleanup_output
    assert (await _hashes_by_id(session_factory))[ids[0]] == hashlib.sha256(content).hexdigest()

    monkeypatch.setattr(command, "_dispose_engine", real_dispose)
    r2_settings = Settings(
        _env_file=None,
        database_url=scratch_url,
        data_dir=data_dir,
        storage_backend="r2",
    )
    monkeypatch.setattr(command, "get_settings", lambda: r2_settings)
    storage_code = await command.run(
        through_id=ids[-1],
        after_id=0,
        batch_size=2,
        apply=False,
    )
    storage_output = capsys.readouterr().out
    assert storage_code == 2
    assert json.loads(storage_output) == {
        "status": "fatal",
        "code": "configuration_or_storage_initialization_failure",
        "message": "Backfill could not produce a trustworthy batch result.",
    }
    assert scratch_url not in storage_output
