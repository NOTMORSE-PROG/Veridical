"""F1/F2 admission must outlive cancelled asyncio wrappers and request owners."""

import asyncio
import threading
import time
from contextlib import asynccontextmanager, suppress
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.errors import DocumentPreparationBusyError
from app.ingest import admission as registry
from app.ingest.admission import PreparationAdmission

DEADLINE = 2.0


@pytest.fixture
def admission():
    controller = PreparationAdmission(Settings(_env_file=None))
    yield controller
    controller.shutdown()
    assert controller.drained


async def entered(event):
    assert await asyncio.to_thread(event.wait, DEADLINE)


async def drained(controller):
    async def wait():
        while not controller.drained:
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait(), DEADLINE)


def blocked_work(started, release, *, fail=False):
    started.set()
    assert release.wait(DEADLINE), "Test must release every controlled native job"
    if fail:
        raise ValueError("Private source exception must not be logged")
    return "finished"


@pytest.mark.parametrize("invalid", [0, 2])
def test_only_serial_preparation_is_supported_by_current_threaded_topology(invalid):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, document_preparation_slots=invalid)


async def test_owner_retains_admission_between_native_phases(admission):
    with admission.acquire() as lease:
        assert await lease.run(lambda: "first") == "first"
        with pytest.raises(DocumentPreparationBusyError):
            admission.acquire()
        assert await lease.run(lambda: "second") == "second"
    assert admission.drained
    with admission.acquire() as lease:
        assert await lease.run(lambda: "next owner") == "next owner"


@pytest.mark.parametrize("mode", ["cancel", "timeout", "late_failure"])
async def test_awaiter_exit_cannot_release_running_native_work(admission, mode, caplog):
    started, release = threading.Event(), threading.Event()
    task = None
    try:
        with admission.acquire() as lease:
            task = asyncio.create_task(
                lease.run(blocked_work, started, release, fail=mode == "late_failure")
            )
            await entered(started)
            if mode == "timeout":
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(task, 0.01)
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert task.done() and task.cancelled()
        assert admission.active_owners == 1
        with pytest.raises(DocumentPreparationBusyError):
            admission.acquire()
        release.set()
        await drained(admission)
        with admission.acquire() as next_lease:
            assert await next_lease.run(lambda: "subsequent owner") == "subsequent owner"
        assert "Private source exception" not in caplog.text
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
        await drained(admission)


async def test_one_owner_cannot_accumulate_an_executor_queue(admission):
    started, release = threading.Event(), threading.Event()
    try:
        with admission.acquire() as lease:
            first = asyncio.create_task(lease.run(blocked_work, started, release))
            await entered(started)
            with pytest.raises(RuntimeError, match="already has native work"):
                await lease.run(lambda: pytest.fail("Second callable must not be submitted"))
            release.set()
            assert await first == "finished"
    finally:
        release.set()
        await drained(admission)


async def test_submission_failure_does_not_leak_admission(admission, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Executor submission unavailable")

    monkeypatch.setattr(admission._executor, "submit", fail)
    with pytest.raises(RuntimeError, match="submission unavailable"), admission.acquire() as lease:
        await lease.run(lambda: None)
    assert admission.drained


async def test_native_exception_releases_after_owner_exit(admission):
    def fail():
        raise ValueError("Expected native failure")

    with pytest.raises(ValueError, match="Expected native failure"), admission.acquire() as lease:
        await lease.run(fail)
    assert admission.drained


@pytest.mark.parametrize("kind", ["function", "callable", "returned_coroutine"])
async def test_async_work_cannot_escape_as_an_unexecuted_result(admission, kind):
    calls = []

    async def asynchronous():
        calls.append("escaped async work")

    class AsyncCallable:
        async def __call__(self):
            calls.append("escaped callable work")

    function = (
        asynchronous
        if kind == "function"
        else AsyncCallable()
        if kind == "callable"
        else lambda: asynchronous()
    )
    with pytest.raises(TypeError, match="requires synchronous work"), admission.acquire() as lease:
        await lease.run(function)
    assert calls == [] and admission.drained


def test_one_controller_can_serve_two_sequential_event_loops(admission):
    for value in ("first loop", "second loop"):
        with admission.acquire() as lease:
            assert asyncio.run(lease.run(lambda current=value: current)) == value
        assert admission.drained


def test_native_completion_after_request_loop_closed_needs_no_loop(admission):
    started, release = threading.Event(), threading.Event()
    loop = asyncio.new_event_loop()
    task = None

    async def owner():
        with admission.acquire() as lease:
            await lease.run(blocked_work, started, release)

    try:
        task = loop.create_task(owner())
        loop.run_until_complete(entered(started))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(task)
        loop.close()
        assert admission.active_owners == 1
        release.set()
        deadline = time.monotonic() + DEADLINE
        while not admission.drained and time.monotonic() < deadline:
            time.sleep(0.005)
        assert admission.drained
    finally:
        release.set()
        if not loop.is_closed():
            if task is not None and not task.done():
                task.cancel()
            loop.close()


async def test_shutdown_does_not_claim_running_work_has_terminated(admission):
    started, release = threading.Event(), threading.Event()
    try:
        with admission.acquire() as lease:
            task = asyncio.create_task(lease.run(blocked_work, started, release))
            await entered(started)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        admission.shutdown()
        assert not admission.drained
        with pytest.raises(RuntimeError, match="admission is closed"):
            admission.acquire()
        release.set()
        await drained(admission)

    finally:
        release.set()
        await drained(admission)


@pytest.fixture
def registry_settings():
    settings = Settings(_env_file=None)
    assert registry._managed_lifespan is None
    if registry._current is not None:
        assert registry._current.drained
    yield settings
    assert registry._managed_lifespan is None
    if registry._current is not None:
        registry._current.shutdown()
        assert registry._current.drained


def close_lifespan(handle):
    registry.begin_preparation_shutdown(handle)
    registry.finish_preparation_lifespan(handle)


@pytest.mark.parametrize("path", ["direct", "failed", "activation"])
async def test_removed_library_cleanup_retains_admission_past_owner_cancellation(
    registry_settings, monkeypatch, path
):
    """Real cleanup dispatch, scripted DB only; blocked harmless file operation."""
    from app.library import service as library
    from app.models.enums import LibraryEntrySource, LibraryEntryStatus

    started, release = threading.Event(), threading.Event()
    entry = SimpleNamespace(
        status=LibraryEntryStatus.removed, source=LibraryEntrySource.library_submission
    )
    manuscript = SimpleNamespace(id=1, purged_at=None)

    class Session:
        async def refresh(self, *args, **kwargs):
            pass

        async def commit(self):
            pytest.fail("Cancellation must reach cleanup before the commit")

    @asynccontextmanager
    async def lock(*args):
        yield

    async def archive(*args):
        pass

    monkeypatch.setattr(library, "advisory_lock", lock)
    monkeypatch.setattr(library, "delete_archive_rows", archive)
    monkeypatch.setattr(
        library, "delete_stored_files", lambda *args: blocked_work(started, release)
    )
    runtime = registry.get_preparation_admission(registry_settings)

    async def owner():
        with registry.preparation_scope(registry_settings) as lease:
            args = (Session(), entry, manuscript)
            if path == "activation":
                return await library._activate(
                    *args,
                    None,
                    [],
                    registry_settings,
                    expected=LibraryEntryStatus.processing,
                    decision=None,
                    preparation_lease=lease,
                )
            if path == "failed":
                return await library._fail(
                    *args, "Controlled failure", registry_settings, preparation_lease=lease
                )
            return await library._discard_late_work(
                *args, registry_settings, preparation_lease=lease
            )

    task = asyncio.create_task(owner())
    try:
        await entered(started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not runtime.drained and runtime.active_owners == 1
        with (
            pytest.raises(DocumentPreparationBusyError),
            registry.preparation_scope(registry_settings),
        ):
            pytest.fail("The next owner must wait for actual storage cleanup to finish")
    finally:
        release.set()
        with suppress(asyncio.CancelledError):
            await task
        await drained(runtime)
    with registry.preparation_scope(registry_settings) as lease:
        assert await lease.run(lambda: "explicit later work") == "explicit later work"


def test_direct_calls_and_managed_start_adopt_one_controller(registry_settings):
    first = registry.get_preparation_admission(registry_settings)
    with registry.preparation_scope(registry_settings) as lease:
        for value in ("first", "second"):
            assert asyncio.run(lease.run(lambda current=value: current)) == value
        handle = registry.start_preparation_admission(registry_settings)
        try:
            assert handle.controller is first
            with (
                pytest.raises(DocumentPreparationBusyError),
                registry.preparation_scope(registry_settings),
            ):
                pytest.fail("A second preparation owner must not be admitted")
        finally:
            close_lifespan(handle)
    assert first.drained


def test_closing_window_rejects_even_after_drain(registry_settings):
    handle = registry.start_preparation_admission(registry_settings)
    registry.begin_preparation_shutdown(handle)
    try:
        assert handle.controller.drained and not handle.controller.accepting
        with pytest.raises(DocumentPreparationBusyError, match="unavailable"):
            registry.get_preparation_admission(registry_settings)
        assert registry._current is handle.controller
    finally:
        registry.finish_preparation_lifespan(handle)
    replacement = registry.get_preparation_admission(registry_settings)
    assert replacement is not handle.controller and replacement.accepting


def test_stale_and_overlapping_lifespans_cannot_close_current_registry(registry_settings):
    first = registry.start_preparation_admission(registry_settings)
    try:
        with pytest.raises(RuntimeError, match="managed lifespan"):
            registry.start_preparation_admission(registry_settings)
    finally:
        close_lifespan(first)
    second = registry.start_preparation_admission(registry_settings)
    try:
        assert second.controller is not first.controller
        with pytest.raises(RuntimeError, match="no longer current"):
            registry.begin_preparation_shutdown(first)
        with pytest.raises(RuntimeError, match="not closing"):
            registry.finish_preparation_lifespan(first)
        assert registry.get_preparation_admission(registry_settings) is second.controller
        with registry.preparation_scope(registry_settings) as lease:
            assert asyncio.run(lease.run(lambda: "still usable")) == "still usable"
    finally:
        close_lifespan(second)


async def test_retiring_controller_cannot_be_replaced_while_native_work_lives(registry_settings):
    started, release = threading.Event(), threading.Event()
    handle = registry.start_preparation_admission(registry_settings)
    old = handle.controller
    try:
        with registry.preparation_scope(registry_settings) as lease:
            task = asyncio.create_task(lease.run(blocked_work, started, release))
            await entered(started)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        close_lifespan(handle)
        assert not old.drained and not old.accepting
        with pytest.raises(DocumentPreparationBusyError, match="unavailable"):
            registry.get_preparation_admission(registry_settings)
        with pytest.raises(RuntimeError, match="still draining"):
            registry.start_preparation_admission(registry_settings)
        assert registry._current is old
    finally:
        release.set()
        if registry._managed_lifespan is handle:
            close_lifespan(handle)
        await drained(old)
    new = registry.start_preparation_admission(registry_settings)
    try:
        assert new.controller is not old
        with registry.preparation_scope(registry_settings) as lease:
            assert await lease.run(lambda: "replacement after drain") == "replacement after drain"
    finally:
        close_lifespan(new)


async def test_nested_preparation_borrows_lease_without_finishing_owner(registry_settings):
    with registry.preparation_scope(registry_settings) as outer:
        with registry.preparation_scope(registry_settings, outer) as inner:
            assert inner is outer
            assert await inner.run(lambda: "nested") == "nested"
        with (
            pytest.raises(DocumentPreparationBusyError),
            registry.preparation_scope(registry_settings),
        ):
            pytest.fail("Borrowing must not close the outer owner")
        assert await outer.run(lambda: "outer continues") == "outer continues"
    assert registry.get_preparation_admission(registry_settings).drained


def test_a_foreign_or_finished_lease_cannot_bypass_shared_admission(registry_settings, admission):
    registry.get_preparation_admission(registry_settings)
    with (
        admission.acquire() as foreign,
        pytest.raises(RuntimeError, match="another controller"),
        registry.preparation_scope(registry_settings, foreign),
    ):
        pytest.fail("An unrelated controller cannot provide a shared service lease")
    with registry.preparation_scope(registry_settings) as closed:
        pass
    with (
        pytest.raises(RuntimeError, match="not active"),
        registry.preparation_scope(registry_settings, closed),
    ):
        pytest.fail("A finished lease cannot be borrowed")
