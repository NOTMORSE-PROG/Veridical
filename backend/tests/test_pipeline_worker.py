"""BUG-032 finding 3: `worker_loop` must survive a bad tick, not die
permanently and silently. No DB needed — `worker_tick` itself is
monkeypatched, so this is a pure unit test of the supervision behavior
(unlike the other `test_pipeline_*_live.py` files)."""

import asyncio

import pytest

from app.config import get_settings
from app.pipeline import worker
from app.pipeline.coordination import AdmissionState


async def test_worker_loop_logs_and_keeps_polling_after_a_bad_tick(monkeypatch, caplog):
    calls = 0

    async def flaky_tick(session_factory, settings):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated: transient DB error mid-tick")
        return AdmissionState.idle

    monkeypatch.setattr(worker, "worker_step", flaky_tick)

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError  # stop the otherwise-infinite loop

    with caplog.at_level("ERROR"), pytest.raises(asyncio.CancelledError):
        await worker.worker_loop(settings=get_settings(), sleep=fake_sleep)

    # The loop survived the RuntimeError and ticked again, instead of dying.
    assert calls == 2
    # Backed off (not hot-looped) after the failed tick, same as a genuinely
    # idle tick — and logged it, so a real deploy has SOME trace of it
    # (the ticket's own "panel review confirmed the traceback in docker logs"
    # diagnostic signal must not vanish just because it's now caught).
    assert sleeps[0] == get_settings().pipeline_worker_poll_seconds
    assert "worker_step failed" in caplog.text


async def test_worker_loop_does_not_swallow_cancellation(monkeypatch):
    """A real shutdown (`task.cancel()` in app.main's lifespan) must still
    stop the loop — the new except Exception must not catch CancelledError."""

    async def cancels_immediately(session_factory, settings):
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, "worker_step", cancels_immediately)

    with pytest.raises(asyncio.CancelledError):
        await worker.worker_loop(settings=get_settings(), sleep=asyncio.sleep)


async def test_nonperiodic_scheduler_waits_for_wake_then_drains(monkeypatch):
    wake, drained = asyncio.Event(), asyncio.Event()
    calls = 0

    async def step(*_args):
        nonlocal calls
        calls += 1
        assert not wake.is_set()  # Wake clears before inspecting persisted work.
        if calls == 1:
            return AdmissionState.admitted
        drained.set()
        return AdmissionState.idle

    monkeypatch.setattr(worker, "worker_step", step)
    task = asyncio.create_task(worker.worker_loop(wake_event=wake, periodic=False))
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert calls == 0
        wake.set()
        await asyncio.wait_for(drained.wait(), 1)
        assert calls == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_wake_during_final_idle_inspection_is_not_lost(monkeypatch):
    wake, inspecting, release, second = (asyncio.Event() for _ in range(4))
    calls = 0

    async def step(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            inspecting.set()
            await release.wait()
        else:
            second.set()
        return AdmissionState.idle

    monkeypatch.setattr(worker, "worker_step", step)
    task = asyncio.create_task(worker.worker_loop(wake_event=wake, periodic=False))
    try:
        wake.set()
        await asyncio.wait_for(inspecting.wait(), 1)
        wake.set()
        release.set()
        await asyncio.wait_for(second.wait(), 1)
        assert calls == 2
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_busy_wake_is_retained_until_queue_drains(monkeypatch):
    wake, drained = asyncio.Event(), asyncio.Event()
    states = iter((AdmissionState.busy, AdmissionState.admitted, AdmissionState.idle))
    calls, sleeps = [], []

    async def step(*_args):
        state = next(states)
        calls.append(state)
        if state == AdmissionState.idle:
            drained.set()
        return state

    async def sleep(seconds):
        sleeps.append(seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(worker, "worker_step", step)
    task = asyncio.create_task(worker.worker_loop(wake_event=wake, periodic=False, sleep=sleep))
    try:
        wake.set()
        await asyncio.wait_for(drained.wait(), 1)
        assert calls == [AdmissionState.busy, AdmissionState.admitted, AdmissionState.idle]
        assert sleeps == [get_settings().pipeline_worker_poll_seconds, 0]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_periodic_scheduler_recovers_without_request_wake(monkeypatch):
    inspected = asyncio.Event()

    async def step(*_args):
        inspected.set()
        return AdmissionState.idle

    monkeypatch.setattr(worker, "worker_step", step)
    task = asyncio.create_task(worker.worker_loop(wake_event=asyncio.Event(), periodic=True))
    try:
        await asyncio.wait_for(inspected.wait(), 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
