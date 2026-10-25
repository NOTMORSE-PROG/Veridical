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


@pytest.mark.parametrize("status", [400, 401, 403, 410, 422])
async def test_rejected_lookup_is_unavailable_without_retry_or_absence(status):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(status, text="private-response-sentinel")

    async with _client(handler) as client:
        with pytest.raises(ApiDownError) as error:
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
    assert str(status) in str(error.value)
    assert "private-response-sentinel" not in str(error.value)
    assert "https://" not in str(error.value)


class CountedGovernor:
    def __init__(self):
        self.acquisitions = 0

    async def acquire(self):
        self.acquisitions += 1


@pytest.mark.parametrize(
    "body", [b"<html>unavailable</html>", b"null", b"[]", b'"value"', b"123", b"\xff"]
)
@pytest.mark.parametrize("retries", [0, 2])
async def test_invalid_object_response_exhausts_only_configured_budget(body, retries):
    governor, sleeps, requests = CountedGovernor(), [], []

    async def sleep(seconds):
        sleeps.append(seconds)

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=body)

    async with _client(handler) as client:
        with pytest.raises(ApiDownError):
            await get_json(
                client,
                "https://example.org/object",
                provider="test",
                governor=governor,
                max_retries=retries,
                retry_base_seconds=0.5,
                sleep=sleep,
            )
    assert len(requests) == governor.acquisitions == retries + 1
    assert sleeps == [0.5 * 2**attempt for attempt in range(retries)]


@pytest.mark.parametrize("body", [b"<html>temporary failure</html>", b"null", b"[]"])
async def test_invalid_response_can_recover_without_losing_the_object(body):
    governor, sleeps, requests = CountedGovernor(), [], []
    expected = {"title": "Controlled book", "nested": [1, 2]}

    async def sleep(seconds):
        sleeps.append(seconds)

    def handler(request):
        requests.append(request)
        return (
            httpx.Response(200, content=body)
            if len(requests) == 1
            else httpx.Response(200, json=expected)
        )

    async with _client(handler) as client:
        actual = await get_json(
            client,
            "https://example.org/object",
            provider="test",
            governor=governor,
            max_retries=2,
            retry_base_seconds=0.5,
            sleep=sleep,
        )
    assert actual == expected
    assert len(requests) == governor.acquisitions == 2
    assert sleeps == [0.5]


@pytest.mark.parametrize(
    "failure", [httpx.ReadTimeout, httpx.DecodingError, httpx.TooManyRedirects]
)
async def test_request_failures_remain_bounded_and_typed(failure):
    governor, sleeps, requests = CountedGovernor(), [], []

    async def sleep(seconds):
        sleeps.append(seconds)

    def handler(request):
        requests.append(request)
        raise failure("Controlled request failure", request=request)

    async with _client(handler) as client:
        with pytest.raises(ApiDownError):
            await get_json(
                client,
                "https://example.org/object",
                provider="test",
                governor=governor,
                max_retries=2,
                retry_base_seconds=0.5,
                sleep=sleep,
            )
    assert len(requests) == governor.acquisitions == 3
    assert sleeps == [0.5, 1.0]
