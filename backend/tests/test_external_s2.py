"""V-028: Semantic Scholar client — recorded response (real shape captured
live 2026-08-06 against /paper/DOI:10.1038/nature12373, see s2.py's module
docstring), no network in CI."""

import httpx

from app.config import get_settings
from app.external import s2

_REAL_PAPER = {
    "paperId": "a5de30adc5c22bc86e8cfabe7fbd07c052d196a8",
    "title": "Nanometer scale thermometry in a living cell",
    "externalIds": {"DOI": "10.1038/nature12373"},
    "isOpenAccess": True,
    "openAccessPdf": {"url": "https://www.nature.com/articles/nature12373.pdf"},
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_lookup_doi_found():
    def handler(request):
        return httpx.Response(200, json=_REAL_PAPER)

    async with _client(handler) as client:
        result = await s2.lookup_doi(client, "10.1038/nature12373", settings=get_settings())
    assert result.found
    assert result.title == "Nanometer scale thermometry in a living cell"
    assert result.url == "https://www.nature.com/articles/nature12373.pdf"
    assert not result.retracted  # S2 has no retraction concept


async def test_lookup_doi_not_found():
    def handler(request):
        return httpx.Response(404)

    async with _client(handler) as client:
        result = await s2.lookup_doi(client, "10.9999/nope", settings=get_settings())
    assert not result.found


async def test_no_key_sends_no_api_key_header():
    seen = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json=_REAL_PAPER)

    async with _client(handler) as client:
        await s2.lookup_doi(client, "10.1038/nature12373", settings=get_settings())
    assert "x-api-key" not in seen["headers"]


async def test_search_by_title_wraps_data_list():
    def handler(request):
        return httpx.Response(200, json={"total": 1, "offset": 0, "data": [_REAL_PAPER]})

    async with _client(handler) as client:
        result = await s2.search_by_title(
            client, "Nanometer scale thermometry", settings=get_settings()
        )
    assert result.found
    assert result.title == _REAL_PAPER["title"]


async def test_search_by_title_empty():
    def handler(request):
        return httpx.Response(200, json={"total": 0, "offset": 0, "data": []})

    async with _client(handler) as client:
        result = await s2.search_by_title(client, "nothing matches", settings=get_settings())
    assert not result.found
