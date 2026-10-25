"""V-009: RateGovernor throttling — pure unit tests, no DB.

Uses an injectable fake clock/sleep so a 50-call burst is provable without
waiting real wall-clock minutes (RPM windows are 60s).
"""

import asyncio

import pytest

from app.rate_governor import RateGovernor


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


async def test_minimum_interval_paces_concurrent_admissions_without_initial_wait():
    clock = FakeClock()
    governor = RateGovernor(rpm=60, min_interval_seconds=0.5, clock=clock.now, sleep=clock.sleep)
    times = []

    async def acquire():
        await governor.acquire()
        times.append(clock.now())

    await asyncio.gather(*(acquire() for _ in range(4)))
    assert times == [0, 0.5, 1, 1.5]


async def test_tightening_interval_preserves_previous_admission():
    clock = FakeClock()
    governor = RateGovernor(rpm=60, min_interval_seconds=0.25, clock=clock.now, sleep=clock.sleep)
    await governor.acquire()
    governor.tighten_min_interval(1)
    await governor.acquire()
    assert clock.now() == 1
    governor.tighten_min_interval(0.25)
    await governor.acquire()
    assert clock.now() == 2


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_minimum_interval_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        RateGovernor(rpm=60, min_interval_seconds=value)
