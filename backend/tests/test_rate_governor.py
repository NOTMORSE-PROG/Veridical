"""V-009: RateGovernor throttling — pure unit tests, no DB.

Uses an injectable fake clock/sleep so a 50-call burst is provable without
waiting real wall-clock minutes (RPM windows are 60s).
"""

import asyncio

from app.llm.queue import RateGovernor


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds


def _max_calls_in_any_60s_window(timestamps: list[float]) -> int:
    return max(sum(1 for other in timestamps if t - 60.0 < other <= t) for t in timestamps)


async def test_burst_of_50_sequential_acquires_never_exceeds_rpm_window():
    clock = FakeClock()
    governor = RateGovernor(rpm=15, clock=clock.now, sleep=clock.sleep)
    timestamps = []
    for _ in range(50):
        await governor.acquire()
        timestamps.append(clock.now())
    assert len(timestamps) == 50
    assert _max_calls_in_any_60s_window(timestamps) <= 15


async def test_burst_of_50_concurrent_acquires_never_exceeds_rpm_window():
    """The AC scenario: 50 calls queued at once (not one-at-a-time)."""
    clock = FakeClock()
    governor = RateGovernor(rpm=15, clock=clock.now, sleep=clock.sleep)
    timestamps: list[float] = []

    async def acquire_and_record() -> None:
        await governor.acquire()
        timestamps.append(clock.now())

    await asyncio.gather(*(acquire_and_record() for _ in range(50)))
    assert len(timestamps) == 50
    assert _max_calls_in_any_60s_window(timestamps) <= 15


async def test_calls_within_the_rpm_budget_do_not_wait():
    clock = FakeClock()
    governor = RateGovernor(rpm=15, clock=clock.now, sleep=clock.sleep)
    for _ in range(15):
        await governor.acquire()
    assert clock.t == 0.0  # never had to sleep — under budget the whole time
