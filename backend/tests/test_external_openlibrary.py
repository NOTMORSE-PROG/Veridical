"""V-028: Open Library client — recorded responses (real shapes captured
live 2026-08-06, see openlibrary.py's module docstring), no network in CI."""

import httpx

from app.config import get_settings
from app.external import openlibrary

_REAL_EDITION = {
    "key": "/books/OL31838212M",
    "title": "Effective Java",
    "works": [{"key": "/works/OL6223299W"}],
    "isbn_13": ["9780134685991"],
}

_REAL_SEARCH_DOC = {
    "key": "/works/OL6223299W",
    "title": "Effective Java",
    "author_name": ["Joshua Bloch"],
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_lookup_isbn_found_is_existence_only():
    def handler(request):
        return httpx.Response(200, json=_REAL_EDITION)

    async with _client(handler) as client:
        result = await openlibrary.lookup_isbn(client, "9780134685991", settings=get_settings())
    assert result.found
    assert result.title == "Effective Java"
    assert not result.content_checkable  # ticket AC: books are existence-only
    assert result.url == "https://openlibrary.org/books/OL31838212M"


async def test_lookup_isbn_not_found():
    def handler(request):
        return httpx.Response(404)

    async with _client(handler) as client:
        result = await openlibrary.lookup_isbn(client, "0000000000", settings=get_settings())
    assert not result.found


async def test_search_title_found():
    def handler(request):
        return httpx.Response(200, json={"docs": [_REAL_SEARCH_DOC], "numFound": 1})

    async with _client(handler) as client:
        result = await openlibrary.search_title(client, "Effective Java", settings=get_settings())
    assert result.found
    assert result.title == "Effective Java"
    assert not result.content_checkable


async def test_search_title_no_results():
    def handler(request):
        return httpx.Response(200, json={"docs": [], "numFound": 0})

    async with _client(handler) as client:
        result = await openlibrary.search_title(
            client, "a title that does not exist", settings=get_settings()
        )
    assert not result.found


async def test_identifying_user_agent_sent():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json=_REAL_EDITION)

    async with _client(handler) as client:
        await openlibrary.lookup_isbn(client, "9780134685991", settings=get_settings())
    assert "mailto:" in seen["ua"]
