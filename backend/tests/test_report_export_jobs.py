"""F8.6: real task/file lifetime with a controlled blocked renderer."""

import asyncio
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

import app.report.export_jobs as module
from app.errors import ConflictError, GoneError, NotFoundError, RateLimitedError


@pytest.fixture
def runtime(monkeypatch):
    binding = module.ExportBinding(1, 30, 0, 22, "source-hash", "source.pdf", None)
    state = {"binding": binding, "calls": 0, "admitted": 0}
    entered, release = threading.Event(), threading.Event()

    @asynccontextmanager
    async def session():
        yield object()

    async def current(*_):
        return state["binding"]

    async def assemble(*_):
        return SimpleNamespace(report=SimpleNamespace(assessment_revision=0))

    def build(_):
        state["calls"] += 1
        entered.set()
        assert release.wait(10)
        return b"controlled artifact bytes"

    monkeypatch.setattr(module, "get_session_factory", lambda: session)
    monkeypatch.setattr(module, "export_binding", current)
    monkeypatch.setattr(module, "get_report_export_data", assemble)
    monkeypatch.setattr(module, "build_report_pdf", build)
    manager = module.ExportJobs()
    yield manager, binding, state, entered, release
    release.set()
    if manager.expiry:
        manager.expiry.cancel()
    if manager.job:
        manager.job.cleanup()


async def wait_entered(entered):
    for _ in range(1000):
        if entered.is_set():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("Worker did not enter controlled renderer")


@pytest.mark.asyncio
async def test_prepare_status_coalesce_and_busy_while_thread_is_blocked(runtime):
    manager, binding, state, entered, release = runtime
    job = manager.prepare(binding)
    await wait_entered(entered)
    assert not job.task.done() and manager.find(job.id, binding).state == "preparing"
    assert manager.prepare(binding) is job and state["calls"] == 1
    with pytest.raises(RateLimitedError):
        manager.prepare(replace(binding, owner=2))
    release.set()
    await job.task
    assert job.state == "ready" and job.path.read_bytes() == b"controlled artifact bytes"
    assert manager.prepare(binding) is job and state["calls"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [("revision", 1), ("purged_at", "purged"), ("content_hash", "changed"), ("file_ref", "other")],
)
async def test_source_or_revision_change_during_generation_withholds_artifact(
    runtime, field, value
):
    manager, binding, state, entered, release = runtime
    job = manager.prepare(binding)
    await wait_entered(entered)
    state["binding"] = replace(binding, **{field: value})
    release.set()
    await job.task
    assert job.state == "stale" and job.path is None


@pytest.mark.asyncio
async def test_foreign_job_and_stale_ready_source_never_return_artifact(runtime):
    manager, binding, _, entered, release = runtime
    job = manager.prepare(binding)
    await wait_entered(entered)
    release.set()
    await job.task
    for foreign in [replace(binding, owner=2), replace(binding, run=31)]:
        with pytest.raises(NotFoundError):
            manager.find(job.id, foreign)
    with pytest.raises(ConflictError):
        manager.find(job.id, replace(binding, purged_at="purged"))
    with pytest.raises(GoneError):
        module.ExportJobs().find(job.id, binding)


@pytest.mark.asyncio
async def test_cancel_does_not_release_thread_admission_until_it_drains(runtime):
    manager, binding, _, entered, release = runtime
    job = manager.prepare(binding)
    await wait_entered(entered)
    import app.report.evidence_images as images

    active = images._active_readers
    job.task.cancel()
    await asyncio.sleep(0.01)
    assert not job.task.done() and images._active_readers == active
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await job.task
    assert job.state == "failed" and images._active_readers == active - 1


@pytest.mark.asyncio
async def test_expiry_keeps_leased_file_then_cleans_and_replacement_is_bounded(runtime):
    manager, binding, _, entered, release = runtime
    job = manager.prepare(binding)
    await wait_entered(entered)
    release.set()
    await job.task
    path = job.path
    job.finished -= module.get_settings().export_job_ttl_seconds + 1
    job.leases = 1
    manager.expire()
    assert path.exists()
    with pytest.raises(RateLimitedError):
        manager.prepare(replace(binding, run=31))
    job.leases = 0
    manager.expire()
    assert not path.exists() and manager.job is None


@pytest.mark.asyncio
async def test_size_refusal_and_initial_purged_history(runtime, monkeypatch):
    manager, binding, state, entered, release = runtime
    binding = replace(binding, purged_at="already purged")
    state["binding"] = binding
    settings = module.get_settings().model_copy(update={"export_job_max_bytes": 1})
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    job = manager.prepare(binding)
    await wait_entered(entered)
    release.set()
    await job.task
    assert job.state == "stale" and "download limit" in job.message and job.path is None
