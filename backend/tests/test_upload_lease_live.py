"""BUG-526: an upload being prepared when its process dies is retired within a
short lease, and a live preparation is never taken over.

Render deploys overlap: the new instance serves while the old one still
finishes its work for a few seconds. The second worker here runs on its own
thread, event loop and database engine, as that new instance would, and polls
the same database while the first one prepares.
"""

import asyncio
import os
import threading
import time
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.db import sqlalchemy_url
from app.ingest import upload_worker
from app.loop_probe import LoopLagProbe
from app.models.enums import IngestStatus
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.upload_attempt import UploadAttempt

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
SCRATCH_DB = "veridical_uploadleasetest"
INTERRUPTED = "Document processing was interrupted."


@pytest.fixture(scope="module")
def scratch_url():
    from alembic import command
    from tests.test_schema import _admin_execute, _alembic_config, _swap_db

    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    url = _swap_db(base, SCRATCH_DB)
    command.upgrade(_alembic_config(url), "head")
    yield url
    asyncio.run(_admin_execute(base, f'DROP DATABASE "{SCRATCH_DB}" WITH (FORCE)'))


def lease_settings(tmp_path, **overrides):
    values = {
        "direct_upload_lease_seconds": 1.2,
        "direct_upload_heartbeat_seconds": 0.3,
        "event_loop_probe_seconds": 0.05,
    }
    values.update(overrides)
    return Settings(_env_file=None, veridical_fake_llm=True, data_dir=tmp_path).model_copy(
        update=values
    )


class Harness:
    """One session factory per event loop, so each simulated instance uses
    its own engine; and a stand-in preparation each test can script."""

    def __init__(self, url, monkeypatch):
        self.url = url
        self.factories = {}
        self.calls = []
        self.finished = []
        self.checks_started = []
        self.prepare = None
        self.fail_sessions = set()
        self.sessions_opened = 0
        monkeypatch.setattr(upload_worker, "get_session_factory", self.factory)
        monkeypatch.setattr(upload_worker, "_ingest_upload", self._ingest)
        monkeypatch.setattr(
            upload_worker,
            "ingestion_receipt",
            lambda *_values: SimpleNamespace(model_dump=lambda **_kw: {"prepared": True}),
        )

        async def start_check(_session, _instructor, _manuscript, rubric_id):
            self.checks_started.append(rubric_id)
            return None, "recorded by the test"

        monkeypatch.setattr(upload_worker, "_start_requested_check", start_check)

    def bind(self):
        engine = create_async_engine(sqlalchemy_url(self.url))
        self.factories[asyncio.get_running_loop()] = async_sessionmaker(
            engine, expire_on_commit=False
        )
        return engine

    def factory(self):
        real = self.factories[asyncio.get_running_loop()]
        harness = self

        def open_session():
            # Only this instance's sessions are counted (and made to fail).
            if threading.current_thread() is threading.main_thread():
                index = harness.sessions_opened
                harness.sessions_opened += 1
                if index in harness.fail_sessions:
                    raise ConnectionError("database unreachable")
            return real()

        return open_session

    async def _ingest(
        self, session, _chunks, _filename, *_args, upload_attempt, before_finalize, **_kwargs
    ):
        attempt_id = upload_attempt.id
        self.calls.append((threading.current_thread().name, attempt_id))
        # Like real ingestion: a record is created, linked and marked as
        # preparing before the long work starts.
        record = Manuscript(
            instructor_id=upload_attempt.instructor_id,
            group_label="Lease",
            file_ref="",
            ingest_status=IngestStatus.processing,
        )
        session.add(record)
        await session.flush()
        upload_attempt.manuscript_id = record.id
        await session.commit()
        if self.prepare is not None:
            await self.prepare()
        record.ingest_status = IngestStatus.done
        # Where real ingestion marks the manuscript ready.
        await before_finalize(session)
        await session.commit()
        self.finished.append(attempt_id)
        return (record,)


@pytest.fixture
async def harness(scratch_url, monkeypatch):
    h = Harness(scratch_url, monkeypatch)
    engine = h.bind()
    async with h.factories[asyncio.get_running_loop()]() as session:
        await session.execute(text("TRUNCATE upload_attempt, instructor RESTART IDENTITY CASCADE"))
        await session.commit()
    try:
        yield h
    finally:
        await engine.dispose()


async def queued_attempt(h, start_rubric_id=None):
    async with h.factories[asyncio.get_running_loop()]() as session:
        owner = Instructor(email=f"lease-{uuid4().hex[:8]}@test.local", display_name="Lease")
        session.add(owner)
        await session.flush()
        attempt = UploadAttempt(
            id=str(uuid4()),
            instructor_id=owner.id,
            filename="thesis.pdf",
            source_bytes=1024,
            staging_key=f"upload_staging/{uuid4().hex}",
            status="queued",
            start_rubric_id=start_rubric_id,
            expires_at=await session.scalar(select(func.now() + timedelta(hours=1))),
        )
        session.add(attempt)
        await session.commit()
        return attempt.id


async def attempt_row(h, attempt_id):
    async with h.factories[asyncio.get_running_loop()]() as session:
        return await session.get(UploadAttempt, attempt_id)


class OtherInstance:
    """A second worker polling the same database from its own event loop."""

    def __init__(self, h, settings, interval=0.05):
        self.h, self.settings, self.interval = h, settings, interval
        self.stop = threading.Event()
        self.errors = []
        self.thread = threading.Thread(target=self._main, name="other-instance")

    def _main(self):
        async def poll():
            engine = self.h.bind()
            try:
                while not self.stop.is_set():
                    await upload_worker._process_next_upload(self.settings, None)
                    await asyncio.sleep(self.interval)
            finally:
                await engine.dispose()

        try:
            asyncio.run(poll())
        except Exception as exc:  # noqa: BLE001 -- reported by the test
            self.errors.append(exc)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.stop.set()
        self.thread.join(10)
        assert not self.thread.is_alive()
        assert not self.errors, self.errors


@live
async def test_a_preparation_whose_heartbeat_stopped_three_minutes_ago_is_retired(harness):
    """Default timings: a process that died three minutes ago no longer holds
    its upload (it used to hold it for up to 30 minutes)."""
    settings = Settings(_env_file=None)
    attempt_id = await queued_attempt(harness)
    async with harness.factories[asyncio.get_running_loop()]() as session:
        last_beat = func.clock_timestamp() - timedelta(minutes=3)
        await session.execute(
            update(UploadAttempt)
            .where(UploadAttempt.id == attempt_id)
            .values(
                status="processing",
                lease_id=str(uuid4()),
                lease_until=last_beat + timedelta(seconds=settings.direct_upload_lease_seconds),
            )
        )
        await session.commit()
    async with harness.factories[asyncio.get_running_loop()]() as session:
        await upload_worker._retire_interrupted(session)
    row = await attempt_row(harness, attempt_id)
    assert row.status == "failed" and row.error.startswith(INTERRUPTED)


@live
async def test_a_dead_preparation_is_retired_after_its_lease_and_not_before(harness, tmp_path):
    settings = lease_settings(tmp_path)
    attempt_id = await queued_attempt(harness)
    started = asyncio.Event()

    async def prepare():
        started.set()
        await asyncio.Event().wait()

    harness.prepare = prepare
    task = asyncio.create_task(upload_worker._process_next_upload(settings, None))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()  # the process is gone: no heartbeat, no finish
    with pytest.raises(asyncio.CancelledError):
        await task
    died = time.monotonic()

    await upload_worker._process_next_upload(settings, None)
    assert (await attempt_row(harness, attempt_id)).status == "processing"

    while (await attempt_row(harness, attempt_id)).status == "processing":
        assert time.monotonic() - died < settings.direct_upload_lease_seconds + 1.0
        await asyncio.sleep(0.1)
        await upload_worker._process_next_upload(settings, None)
    row = await attempt_row(harness, attempt_id)
    assert row.status == "failed" and row.error.startswith(INTERRUPTED)
    assert time.monotonic() - died >= settings.direct_upload_lease_seconds - 0.3


@live
async def test_overlapping_instance_never_takes_a_live_preparation(harness, tmp_path):
    """Deploy overlap: while the old instance prepares (longer than several
    leases), the new instance polls the same database and must neither claim
    nor retire that attempt."""
    settings = lease_settings(tmp_path)
    attempt_id = await queued_attempt(harness)

    async def prepare():
        await asyncio.sleep(settings.direct_upload_lease_seconds * 3)

    harness.prepare = prepare
    with OtherInstance(harness, settings):
        assert await upload_worker._process_next_upload(settings, None) is True
    row = await attempt_row(harness, attempt_id)
    assert row.status == "done" and row.response == {"prepared": True}
    assert harness.calls == [("MainThread", attempt_id)]


@live
async def test_one_failed_heartbeat_does_not_hand_a_live_preparation_away(harness, tmp_path):
    """A database hiccup on one heartbeat used to end the heartbeat task for
    good; with a short lease the other instance would then retire a
    preparation that is still running."""
    settings = lease_settings(tmp_path)
    attempt_id = await queued_attempt(harness)
    # Session 0 claims the attempt; session 1 is the first heartbeat.
    harness.fail_sessions = {harness.sessions_opened + 1}

    async def prepare():
        await asyncio.sleep(settings.direct_upload_lease_seconds * 3)

    harness.prepare = prepare
    with OtherInstance(harness, settings):
        await upload_worker._process_next_upload(settings, None)
    row = await attempt_row(harness, attempt_id)
    assert row.status == "done"
    assert harness.calls == [("MainThread", attempt_id)]


@live
async def test_a_short_event_loop_stall_does_not_lose_the_upload(harness, tmp_path):
    settings = lease_settings(tmp_path)
    attempt_id = await queued_attempt(harness)

    async def prepare():
        await asyncio.sleep(0.4)
        time.sleep(0.6)  # a GIL-bound step blocking the loop, under the lease
        await asyncio.sleep(0.6)

    harness.prepare = prepare
    with OtherInstance(harness, settings):
        await upload_worker._process_next_upload(settings, None)
    assert (await attempt_row(harness, attempt_id)).status == "done"


@live
async def test_a_stall_longer_than_the_lease_is_retired_and_the_late_finish_is_ignored(
    harness, tmp_path, monkeypatch
):
    """Past the lease the other instance cannot tell a stall from a death; it
    retires the attempt honestly, and the stalled worker's finish must not
    turn that answer back into done."""
    settings = lease_settings(tmp_path)
    attempt_id = await queued_attempt(harness)
    retired = threading.Event()

    async def prepare():
        await asyncio.sleep(0.1)
        deadline = time.monotonic() + settings.direct_upload_lease_seconds * 4
        while not retired.is_set() and time.monotonic() < deadline:
            time.sleep(0.05)  # the loop stays blocked; no heartbeat can run

    original = upload_worker._retire_interrupted

    async def observed_retire(session):
        await original(session)
        if threading.current_thread() is not threading.main_thread():
            async with harness.factories[asyncio.get_running_loop()]() as observer:
                if (await observer.get(UploadAttempt, attempt_id)).status == "failed":
                    retired.set()

    monkeypatch.setattr(upload_worker, "_retire_interrupted", observed_retire)
    harness.prepare = prepare
    with OtherInstance(harness, settings):
        await upload_worker._process_next_upload(settings, None)
    assert retired.is_set()
    row = await attempt_row(harness, attempt_id)
    assert row.status == "failed" and row.error.startswith(INTERRUPTED)
    assert row.response is None
    assert harness.finished == [], "a retired preparation must not mark its record ready"
    assert (await record_of(harness, attempt_id)).ingest_status == IngestStatus.failed


@live
async def test_a_preparation_retired_while_running_stops_and_starts_no_check(harness, tmp_path):
    """Missed heartbeats (a database outage longer than the lease) let the
    other instance retire a preparation that is still running. Once this
    process learns it, the preparation stops: no ready record and no check,
    which would contradict the interrupted answer already shown."""
    settings = lease_settings(tmp_path)
    attempt_id = await queued_attempt(harness, start_rubric_id=7)
    # Session 0 claims; sessions 1-4 are the heartbeats that cannot reach
    # the database (4 x 0.3 s covers the 1.2 s lease).
    first = harness.sessions_opened
    harness.fail_sessions = {first + n for n in range(1, 5)}
    stopped = []

    async def prepare():
        await until_retired(harness, attempt_id)
        # A write this process makes after the retirement (real ingestion
        # marks the record as preparing again before its long work).
        await set_record_status(harness, attempt_id, IngestStatus.processing)
        try:
            await asyncio.sleep(settings.direct_upload_lease_seconds * 4)
        except asyncio.CancelledError:
            stopped.append(time.monotonic())
            raise

    harness.prepare = prepare
    with OtherInstance(harness, settings):
        assert await upload_worker._process_next_upload(settings, None) is True
    assert stopped, "the preparation must be stopped once its lease is gone"
    assert harness.finished == [] and harness.checks_started == []
    row = await attempt_row(harness, attempt_id)
    assert row.status == "failed" and row.error.startswith(INTERRUPTED)
    record = await record_of(harness, attempt_id)
    assert record.ingest_status == IngestStatus.failed, "the record must not stay preparing"
    assert record.ingest_failure_detail.startswith(INTERRUPTED)


@live
async def test_a_hand_off_found_while_recording_it_does_not_stop_the_scheduler(
    harness, tmp_path, monkeypatch
):
    """The preparation finds the retirement itself (at its final step) while
    the heartbeat finds it again during the bookkeeping that follows. That
    second stop must not cancel the scheduler task: nothing would restart
    it, and every later upload would wait forever."""
    settings = lease_settings(tmp_path)
    attempt_id = await queued_attempt(harness)
    beat = settings.direct_upload_heartbeat_seconds
    # The heartbeats cannot reach the database until well after the lease.
    first = harness.sessions_opened
    harness.fail_sessions = {first + n for n in range(1, 7)}

    slow_next_rollback = []

    async def prepare():
        await until_retired(harness, attempt_id)
        slow_next_rollback.append(True)

    original = AsyncSession.rollback

    async def slow_rollback(self):
        # The bookkeeping after the hand-off starts with a rollback; make it
        # span the heartbeat that finds the loss.
        if slow_next_rollback and threading.current_thread() is threading.main_thread():
            slow_next_rollback.clear()
            await asyncio.sleep(beat * 4)
        await original(self)

    monkeypatch.setattr(AsyncSession, "rollback", slow_rollback)
    harness.prepare = prepare
    with OtherInstance(harness, settings):
        assert await upload_worker._process_next_upload(settings, None) is True
    assert asyncio.current_task().cancelling() == 0
    assert harness.finished == []
    assert (await record_of(harness, attempt_id)).ingest_status == IngestStatus.failed


@live
async def test_a_shutdown_arriving_with_a_hand_off_still_stops_the_scheduler(harness, tmp_path):
    """The heartbeat stops the work, and at that moment the server also shuts
    down. The hand-off must not swallow the shutdown."""
    settings = lease_settings(tmp_path)
    attempt_id = await queued_attempt(harness)
    first = harness.sessions_opened
    harness.fail_sessions = {first + n for n in range(1, 5)}
    scheduler = None

    async def prepare():
        await until_retired(harness, attempt_id)
        try:
            await asyncio.sleep(settings.direct_upload_lease_seconds * 4)
        except asyncio.CancelledError:
            scheduler.cancel()  # the shutdown lands together with the stop
            raise

    harness.prepare = prepare
    with OtherInstance(harness, settings):
        scheduler = asyncio.create_task(upload_worker._process_next_upload(settings, None))
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(scheduler, 10)


async def until_retired(h, attempt_id, timeout=10):
    deadline = time.monotonic() + timeout
    while (await attempt_row(h, attempt_id)).status != "failed":
        assert time.monotonic() < deadline, "the other instance never retired the attempt"
        await asyncio.sleep(0.05)


async def record_of(h, attempt_id):
    async with h.factories[asyncio.get_running_loop()]() as session:
        attempt = await session.get(UploadAttempt, attempt_id)
        return await session.get(Manuscript, attempt.manuscript_id)


async def set_record_status(h, attempt_id, status):
    async with h.factories[asyncio.get_running_loop()]() as session:
        attempt = await session.get(UploadAttempt, attempt_id)
        record = await session.get(Manuscript, attempt.manuscript_id)
        record.ingest_status = status
        await session.commit()


async def test_loop_probe_reports_a_blocked_loop():
    probe = LoopLagProbe(0.02).start()
    await asyncio.sleep(0.05)
    time.sleep(0.3)
    await asyncio.sleep(0.05)
    assert probe.stop() >= 0.25


async def test_loop_probe_can_be_turned_off():
    probe = LoopLagProbe(0).start()
    time.sleep(0.05)
    assert probe.stop() == 0.0
