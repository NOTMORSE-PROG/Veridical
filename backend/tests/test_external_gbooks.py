"""V-028: Google Books client — recorded responses + the local daily-quota
degrade (real behavior LIVE-CONFIRMED 2026-08-06: a keyless request gets a
genuine 429 with `quota_limit_value: '0'`, see gbooks.py's module
docstring — not a guess, an observed fact)."""

import httpx
import pytest

from app.config import get_settings
from app.external import gbooks

_REAL_VOLUME = {
    "id": "abc123",
    "volumeInfo": {
        "title": "Effective Java",
        "infoLink": "https://books.google.com/books?id=abc123",
    },
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _reset_module_state():
    gbooks._quota_state["day"] = ""
    gbooks._quota_state["used"] = 0
    yield
    gbooks._quota_state["day"] = ""
    gbooks._quota_state["used"] = 0


async def test_search_title_found_is_existence_only():
    def handler(request):
        return httpx.Response(200, json={"items": [_REAL_VOLUME]})

    async with _client(handler) as client:
        result = await gbooks.search_title(client, "Effective Java", settings=get_settings())
    assert result.found
    assert result.title == "Effective Java"
    assert not result.content_checkable


async def test_search_title_no_results():
    def handler(request):
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        result = await gbooks.search_title(client, "nothing", settings=get_settings())
    assert not result.found


async def test_daily_quota_exhaustion_skips_the_call_entirely():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"items": [_REAL_VOLUME]})

    settings = get_settings().model_copy(update={"google_books_daily_quota": 2})
    async with _client(handler) as client:
        await gbooks.search_title(client, "one", settings=settings)
        await gbooks.search_title(client, "two", settings=settings)
        assert gbooks.quota_exhausted_today(settings)
        result = await gbooks.search_title(client, "three", settings=settings)
    assert calls["n"] == 2  # the third call never reached the network
    assert not result.found
    assert result.raw.get("reason") == "daily_quota_exhausted"


async def test_key_sent_as_query_param_when_configured():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"items": [_REAL_VOLUME]})

    settings = get_settings().model_copy(update={"google_books_api_key": "test-key-123"})
    async with _client(handler) as client:
        await gbooks.search_title(client, "Effective Java", settings=settings)
    assert "key=test-key-123" in seen["url"]
