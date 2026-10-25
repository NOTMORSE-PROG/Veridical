"""F5: observe dispatch times, including redirects, without provider traffic."""

import asyncio

import httpx
import pytest

from app.errors import ApiDownError
from app.external.http import get_json
from app.rate_governor import RateGovernor


class Clock:
    def __init__(self):
        self.now = 0.0

    async def sleep(self, seconds):
        self.now += seconds


async def test_redirects_and_concurrent_lookups_each_wait_before_dispatch():
    clock = Clock()
    times = []
    governor = RateGovernor(
        rpm=60, min_interval_seconds=1, clock=lambda: clock.now, sleep=clock.sleep
    )

    def handler(request):
        times.append(clock.now)
        if request.url.path.startswith("/isbn/"):
            return httpx.Response(302, headers={"location": "/books/edition.json"})
        return httpx.Response(200, json={"title": "Synthetic metadata"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:

        async def lookup(index):
            return await get_json(
                client,
                f"https://openlibrary.org/isbn/{index}.json",
                provider="openlibrary",
                governor=governor,
                max_retries=0,
                retry_base_seconds=0,
                pace_redirects=True,
            )

        results = await asyncio.gather(*(lookup(i) for i in range(2)))
    assert times == [0, 1, 2, 3]
    assert results == [{"title": "Synthetic metadata"}] * 2


@pytest.mark.parametrize("status", [429, 503])
async def test_retry_and_each_redirect_are_paced(status):
    clock = Clock()
    times = []
    governor = RateGovernor(
        rpm=60, min_interval_seconds=1, clock=lambda: clock.now, sleep=clock.sleep
    )

    def handler(request):
        times.append(clock.now)
        if request.url.path.startswith("/isbn/"):
            return httpx.Response(302, headers={"location": "/books/edition.json"})
        return httpx.Response(status if len(times) == 2 else 200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await get_json(
            client,
            "https://openlibrary.org/isbn/fixture.json",
            provider="openlibrary",
            governor=governor,
            max_retries=1,
            retry_base_seconds=0,
            sleep=clock.sleep,
            pace_redirects=True,
        )
    assert result == {"ok": True}
    assert times == [0, 1, 2, 3]


async def test_redirect_loop_is_bounded_and_becomes_unavailable():
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(302, headers={"location": "/loop"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), max_redirects=2) as client:
        with pytest.raises(ApiDownError):
            await get_json(
                client,
                "https://openlibrary.org/loop",
                provider="openlibrary",
                governor=RateGovernor(rpm=60),
                max_retries=0,
                retry_base_seconds=0,
                pace_redirects=True,
            )
    assert len(calls) == 3


async def test_default_other_provider_path_still_obeys_client_redirect_policy():
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(302, headers={"location": "/edition"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        with pytest.raises(ApiDownError):
            await get_json(
                client,
                "https://example.org/start",
                provider="test",
                governor=RateGovernor(rpm=60),
                max_retries=0,
                retry_base_seconds=0,
            )
    assert len(calls) == 1


async def test_cross_origin_redirect_does_not_restore_client_auth_or_cookies():
    seen = []

    def handler(request):
        seen.append(
            (request.url.host, request.headers.get("authorization"), request.headers.get("cookie"))
        )
        if request.url.host == "openlibrary.org":
            return httpx.Response(302, headers={"location": "https://other.example/edition"})
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), auth=("fixture", "fixture")
    ) as client:
        client.cookies.set("private_fixture", "synthetic", domain="openlibrary.org")
        await get_json(
            client,
            "https://openlibrary.org/start",
            provider="openlibrary",
            governor=RateGovernor(rpm=60),
            max_retries=0,
            retry_base_seconds=0,
            pace_redirects=True,
        )
    assert seen[0][1].startswith("Basic ") and seen[0][2] == "private_fixture=synthetic"
    assert seen[1] == ("other.example", None, None)


async def test_relative_redirect_retains_httpx_cookies_and_does_not_reapply_query():
    seen = []

    def handler(request):
        seen.append((request.url.path, str(request.url.query), request.headers.get("cookie")))
        if len(seen) == 1:
            return httpx.Response(
                302, headers={"location": "/edition", "set-cookie": "fixture=synthetic; Path=/"}
            )
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await get_json(
            client,
            "https://openlibrary.org/start",
            provider="openlibrary",
            governor=RateGovernor(rpm=60),
            max_retries=0,
            retry_base_seconds=0,
            params={"title": "Synthetic"},
            pace_redirects=True,
        )
    assert seen[0][1] == "b'title=Synthetic'"
    assert seen[1] == ("/edition", "b''", "fixture=synthetic")


async def test_shared_client_hooks_and_other_provider_governor_unchanged():
    clock = Clock()
    times = []
    book_governor = RateGovernor(
        rpm=60, min_interval_seconds=1, clock=lambda: clock.now, sleep=clock.sleep
    )
    ordinary_governor = RateGovernor(rpm=60, clock=lambda: clock.now, sleep=clock.sleep)

    def handler(request):
        times.append((request.url.host, clock.now))
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        hooks_before = {name: tuple(hooks) for name, hooks in client.event_hooks.items()}
        await get_json(
            client,
            "https://openlibrary.org/start",
            provider="openlibrary",
            governor=book_governor,
            max_retries=0,
            retry_base_seconds=0,
            pace_redirects=True,
        )
        await get_json(
            client,
            "https://example.org/start",
            provider="test",
            governor=ordinary_governor,
            max_retries=0,
            retry_base_seconds=0,
        )
        assert hooks_before == {name: tuple(hooks) for name, hooks in client.event_hooks.items()}
    assert times == [("openlibrary.org", 0), ("example.org", 0)]
