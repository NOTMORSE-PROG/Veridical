"""V-028: the shared get_json retry/backoff helper — no network, no live
services (`httpx.MockTransport` stands in for the recorded-response
cassette the ticket's QA steps ask for)."""

import httpx
import pytest

from app.errors import ApiDownError
from app.external.http import get_json
from app.rate_governor import RateGovernor


async def _noop_sleep(_seconds: float) -> None:
    return None


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_200_returns_json():
    def handler(request):
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as client:
        data = await get_json(
            client,
            "https://example.org/x",
            provider="test",
            governor=RateGovernor(rpm=600),
            max_retries=2,
            retry_base_seconds=0.0,
            sleep=_noop_sleep,
        )
    assert data == {"ok": True}


async def test_404_returns_none_not_an_error():
    def handler(request):
        return httpx.Response(404)

    async with _client(handler) as client:
        data = await get_json(
            client,
            "https://example.org/missing",
            provider="test",
            governor=RateGovernor(rpm=600),
            max_retries=2,
            retry_base_seconds=0.0,
            sleep=_noop_sleep,
        )
    assert data is None


async def test_persistent_500_raises_api_down_after_retries():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500)

    async with _client(handler) as client:
        with pytest.raises(ApiDownError):
            await get_json(
                client,
                "https://example.org/flaky",
                provider="crossref",
                governor=RateGovernor(rpm=600),
                max_retries=2,
                retry_base_seconds=0.0,
                sleep=_noop_sleep,
            )
    assert calls["n"] == 3  # initial attempt + 2 retries


async def test_500_then_200_recovers():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500)
        return httpx.Response(200, json={"recovered": True})

    async with _client(handler) as client:
        data = await get_json(
            client,
            "https://example.org/flaky",
            provider="test",
            governor=RateGovernor(rpm=600),
            max_retries=2,
            retry_base_seconds=0.0,
            sleep=_noop_sleep,
        )
    assert data == {"recovered": True}


async def test_400_raises_immediately_not_retried_as_api_down():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400)

    async with _client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await get_json(
                client,
                "https://example.org/bad-request",
                provider="test",
                governor=RateGovernor(rpm=600),
                max_retries=2,
                retry_base_seconds=0.0,
                sleep=_noop_sleep,
            )
    assert calls["n"] == 1  # a malformed request is never a transient failure
