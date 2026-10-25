"""F5 provider identity is an explicit configuration assertion, never guessed."""

import math

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.errors import ApiDownError
from app.external import openlibrary
from app.rate_governor import RateGovernor


def settings(**kwargs):
    return Settings(_env_file=None, **kwargs)


@pytest.mark.parametrize(
    "contact",
    [
        "",
        "veridical@example.org",
        "x@sub.example.com",
        "x@localhost",
        "bad",
        "bad@host.invalid",
        "a\x00@host.org",
        "a\r\n@host.org",
        "ü@host.org",
        "a@host..org",
        "a@host.org.",
        "a@-host.org",
        "a@host_.org",
        ".a@host.org",
        "a.@host.org",
        "a..b@host.org",
    ],
)
def test_unknown_or_placeholder_contact_never_enables_higher_rate(monkeypatch, contact):
    monkeypatch.setattr(openlibrary, "_governor", None)
    profile = settings(external_contact_email=contact, openlibrary_identified_requests=True)
    governor = openlibrary._get_governor(profile)
    assert governor._min_interval_seconds == 1
    assert openlibrary._headers(profile) == {"User-Agent": "VERIDICAL/1.0"}


def test_real_contact_needs_explicit_opt_in(monkeypatch):
    monkeypatch.setattr(openlibrary, "_governor", None)
    profile = settings(external_contact_email="ops@library.edu.ph")
    assert openlibrary._get_governor(profile)._min_interval_seconds == 1
    assert "mailto:" not in openlibrary._headers(profile)["User-Agent"]


def test_identified_profile_and_configured_fractional_ceiling(monkeypatch):
    monkeypatch.setattr(openlibrary, "_governor", None)
    profile = settings(
        external_contact_email="ops@library.edu.ph",
        openlibrary_identified_requests=True,
        openlibrary_rps=0.5,
    )
    assert openlibrary._get_governor(profile)._min_interval_seconds == 2
    assert openlibrary._headers(profile)["User-Agent"].endswith("(mailto:ops@library.edu.ph)")


@pytest.mark.parametrize("name", ["openlibrary_rps", "openlibrary_unidentified_rps"])
@pytest.mark.parametrize("value", [0, -1, math.inf, math.nan])
def test_invalid_rates_rejected(name, value):
    with pytest.raises(ValidationError):
        settings(**{name: value})


class Clock:
    def __init__(self):
        self.time = 0.0

    async def sleep(self, seconds):
        self.time += seconds


async def test_profile_tightening_retains_one_history(monkeypatch):
    clock = Clock()
    shared = RateGovernor(rpm=180, clock=lambda: clock.time, sleep=clock.sleep)
    monkeypatch.setattr(openlibrary, "_governor", shared)
    identified = settings(
        external_contact_email="ops@library.edu.ph", openlibrary_identified_requests=True
    )
    unknown = settings(external_contact_email="")
    first = openlibrary._get_governor(identified)
    await first.acquire()
    second = openlibrary._get_governor(unknown)
    await second.acquire()
    assert first is second is shared and clock.time == 1 and shared.rpm == 60
    await openlibrary._get_governor(identified).acquire()
    assert clock.time == 2


@pytest.mark.parametrize("fault", [429, 503, "malformed"])
async def test_actual_book_adapter_paces_redirect_and_retry(monkeypatch, fault):
    clock = Clock()
    shared = RateGovernor(rpm=60, clock=lambda: clock.time, sleep=clock.sleep)
    monkeypatch.setattr(openlibrary, "_governor", shared)
    times = []
    profile = settings(
        external_contact_email="", external_max_retries=1, external_retry_base_seconds=0
    )

    def handler(request):
        times.append(clock.time)
        if request.url.path.startswith("/isbn/"):
            return httpx.Response(302, headers={"location": "/books/fixture.json"})
        if len(times) == 2:
            return (
                httpx.Response(200, content="not-json")
                if fault == "malformed"
                else httpx.Response(fault)
            )
        return httpx.Response(200, json={"title": "Synthetic edition", "key": "/books/fixture"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await openlibrary.lookup_isbn(client, "9780140328721", settings=profile)
    assert result.found and not result.content_checkable
    assert times == [0, 1, 2, 3]


async def test_actual_isbn_and_title_share_dispatch_spacing(monkeypatch):
    import asyncio

    clock = Clock()
    monkeypatch.setattr(
        openlibrary, "_governor", RateGovernor(rpm=60, clock=lambda: clock.time, sleep=clock.sleep)
    )
    times = []

    def handler(request):
        times.append(clock.time)
        if request.url.path.startswith("/isbn/"):
            return httpx.Response(302, headers={"location": "/books/fixture.json"})
        if request.url.path == "/search.json":
            return httpx.Response(
                200, json={"docs": [{"title": "Synthetic edition", "key": "/works/fixture"}]}
            )
        return httpx.Response(200, json={"title": "Synthetic edition", "key": "/books/fixture"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await asyncio.gather(
            openlibrary.lookup_isbn(client, "9780140328721", settings=settings()),
            openlibrary.search_title(client, "Synthetic edition", settings=settings()),
        )
    assert all(result.found and not result.content_checkable for result in results)
    assert times == [0, 1, 2]


@pytest.mark.parametrize("status", [400, 404, 422])
async def test_book_refusal_taxonomy_retained(monkeypatch, status):
    monkeypatch.setattr(openlibrary, "_governor", None)
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        if status == 404:
            assert not (await openlibrary.lookup_isbn(client, "fixture", settings=settings())).found
        else:
            with pytest.raises(ApiDownError):
                await openlibrary.lookup_isbn(client, "fixture", settings=settings())
    assert len(calls) == 1
