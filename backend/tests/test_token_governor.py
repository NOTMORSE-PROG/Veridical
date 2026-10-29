"""Full-manuscript prompts are paced under a per-minute input-token budget."""

import json

import pytest

from app.llm.pool import ModelPoolError, load_model_pool
from app.rate_governor import TokenGovernor


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


async def test_calls_within_the_budget_are_not_delayed():
    clock = FakeClock()
    governor = TokenGovernor(250_000, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        await governor.acquire(64_000)
    assert clock.slept == []


async def test_a_call_that_would_exceed_the_budget_waits_for_the_window():
    clock = FakeClock()
    governor = TokenGovernor(250_000, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        await governor.acquire(64_000)
        clock.now += 5
    await governor.acquire(64_000)
    assert clock.slept and clock.now >= 60.0


async def test_a_call_larger_than_the_whole_budget_runs_alone():
    clock = FakeClock()
    governor = TokenGovernor(100_000, clock=clock, sleep=clock.sleep)
    await governor.acquire(150_000)
    assert clock.slept == []
    await governor.acquire(1)
    assert clock.now >= 60.0


def test_invalid_budget_is_refused():
    with pytest.raises(ValueError):
        TokenGovernor(0)


def _pool_file(tmp_path, tpm):
    entry = {"model": "m", "rpm": 5, "daily_quota": 10, "vision": False}
    if tpm is not None:
        entry["tpm"] = tpm
    path = tmp_path / "pool.json"
    path.write_text(json.dumps({"models": [entry]}), encoding="utf-8")
    return path


def test_pool_reads_an_optional_token_budget(tmp_path):
    assert load_model_pool(_pool_file(tmp_path, 250_000))[0].tpm == 250_000
    assert load_model_pool(_pool_file(tmp_path, None))[0].tpm is None


@pytest.mark.parametrize("bad", [0, -1, "250000", 1.5])
def test_pool_refuses_an_invalid_token_budget(tmp_path, bad):
    with pytest.raises(ModelPoolError):
        load_model_pool(_pool_file(tmp_path, bad))


def test_packaged_pool_paces_every_model():
    assert all(spec.tpm for spec in load_model_pool())
