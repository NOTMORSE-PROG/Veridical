"""BUG-032 finding 3: `worker_loop` must survive a bad tick, not die
permanently and silently. No DB needed — `worker_tick` itself is
monkeypatched, so this is a pure unit test of the supervision behavior
(unlike the other `test_pipeline_*_live.py` files)."""

import asyncio

import pytest

from app.config import get_settings
from app.pipeline import worker


async def test_worker_loop_logs_and_keeps_polling_after_a_bad_tick(monkeypatch, caplog):
    calls = 0

    async def flaky_tick(session_factory, settings):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated: transient DB error mid-tick")
        return False  # idle on the next (recovered) tick

    monkeypatch.setattr(worker, "worker_tick", flaky_tick)

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
    # (the ticket's own "professor confirmed the traceback in docker logs"
    # diagnostic signal must not vanish just because it's now caught).
    assert sleeps[0] == get_settings().pipeline_worker_poll_seconds
    assert "worker_tick failed" in caplog.text


async def test_worker_loop_does_not_swallow_cancellation(monkeypatch):
    """A real shutdown (`task.cancel()` in app.main's lifespan) must still
    stop the loop — the new except Exception must not catch CancelledError."""

    async def cancels_immediately(session_factory, settings):
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, "worker_tick", cancels_immediately)

    with pytest.raises(asyncio.CancelledError):
        await worker.worker_loop(settings=get_settings(), sleep=asyncio.sleep)
