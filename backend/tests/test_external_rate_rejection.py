"""A rejected provider is deferred without spending more requests or blocking work."""

import httpx
import pytest

from app.config import Settings
from app.errors import ApiDownError
from app.external import s2
from app.external.http import get_json
from app.rate_governor import RateGovernor


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.mark.parametrize("retry_after", [None, "120", "invalid", "NaN", "Infinity", "-2"])
async def test_first_rate_rejection_stops_retries_and_later_requests_until_reopening(
    monkeypatch, retry_after
):
    monkeypatch.setenv("EXTERNAL_RATE_LIMIT_COOLDOWN_SECONDS", "30")
    clock, requests = Clock(), []
    governor = RateGovernor(rpm=600, clock=clock, sleep=clock.sleep)

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": retry_after} if retry_after is not None else {},
                text="private-provider-body",
            )
        return httpx.Response(200, json={"recovered": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        arguments = dict(
            provider="s2", governor=governor, max_retries=3, retry_base_seconds=1, sleep=clock.sleep
        )
        with pytest.raises(ApiDownError) as error:
            await get_json(client, "https://example.org/paper", **arguments)
        assert len(requests) == 1 and clock.sleeps == []
        assert "429" in str(error.value) and "private-provider-body" not in str(error.value)
        with pytest.raises(ApiDownError):
            await get_json(client, "https://example.org/another-paper", **arguments)
        assert len(requests) == 1 and clock.sleeps == []
        clock.now = 120 if retry_after == "120" else 30
        result = await get_json(client, "https://example.org/paper", **arguments)
        assert result == {"recovered": True} and len(requests) == 2


@pytest.mark.parametrize("rps", [0.5, 1.0, 2.0])
async def test_semantic_scholar_honors_spacing_in_addition_to_the_rolling_window(monkeypatch, rps):
    monkeypatch.setattr(s2, "_governor", None)
    clock = Clock()
    governor = s2._get_governor(Settings(_env_file=None, semantic_scholar_rps=rps))
    governor._clock, governor._sleep = clock, clock.sleep
    await governor.acquire()
    await governor.acquire()
    await governor.acquire()
    assert clock.now == pytest.approx(2 / rps)


async def test_one_provider_deferral_does_not_reset_or_spend_another_provider_budget():
    clock = Clock()
    rejected = RateGovernor(rpm=60, clock=clock)
    independent = RateGovernor(rpm=60, clock=clock)
    rejected.defer_for(30)
    await independent.acquire()
    assert len(independent._timestamps) == 1 and len(rejected._timestamps) == 0
