"""V-028: CrossRef client — recorded responses (real shapes captured live
2026-08-06, see crossref.py's module docstring), no network in CI."""

import httpx

from app.config import get_settings
from app.external import crossref

# Real "update-to" shape, verified live 2026-08-06 against
# https://api.crossref.org/v1/works?filter=update-type:retraction
_RETRACTED_WORK = {
    "message": {
        "DOI": "10.1016/j.example.2020.103768",
        "title": ["RETRACTED: Some paper title"],
        "URL": "https://doi.org/10.1016/j.example.2020.103768",
        "update-to": [
            {
                "DOI": "10.1016/j.example.2020.103768",
                "type": "retraction",
                "label": "Retraction",
                "source": "publisher",
            }
        ],
    }
}

_CORRECTED_WORK = {
    "message": {
        "DOI": "10.1097/example.0000000000005352",
        "title": ["A perfectly fine paper"],
        "URL": "https://doi.org/10.1097/example.0000000000005352",
        "update-to": [
            {
                "DOI": "10.1097/example.0000000000005224",
                "type": "correction",
                "label": "Correction",
                "source": "publisher",
            }
        ],
    }
}

# The 1998 Wakefield-shaped case: no structured update-to, title prefix only.
_OLD_RETRACTION_NO_METADATA = {
    "message": {
        "DOI": "10.1016/S0140-6736(97)11096-0",
        "title": ["RETRACTED: Ileal-lymphoid-nodular hyperplasia"],
        "URL": "https://doi.org/10.1016/S0140-6736(97)11096-0",
    }
}

_CLEAN_WORK = {
    "message": {
        "DOI": "10.1234/clean",
        "title": ["An uncontroversial paper"],
        "URL": "https://doi.org/10.1234/clean",
    }
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_lookup_doi_found_and_clean():
    def handler(request):
        return httpx.Response(200, json=_CLEAN_WORK)

    async with _client(handler) as client:
        result = await crossref.lookup_doi(client, "10.1234/clean", settings=get_settings())
    assert result.found
    assert result.title == "An uncontroversial paper"
    assert not result.retracted
    assert not result.is_correction


async def test_lookup_doi_not_found():
    def handler(request):
        return httpx.Response(404)

    async with _client(handler) as client:
        result = await crossref.lookup_doi(client, "10.9999/nope", settings=get_settings())
    assert not result.found
    assert result.provider == "crossref"


async def test_retraction_detected_from_update_to():
    def handler(request):
        return httpx.Response(200, json=_RETRACTED_WORK)

    async with _client(handler) as client:
        result = await crossref.lookup_doi(client, "x", settings=get_settings())
    assert result.retracted
    assert "Retraction" in result.retraction_detail
    assert not result.is_correction


async def test_correction_is_not_a_retraction():
    """Ticket edge case: 'a correction is info-severity not high' — the two
    must never be conflated."""

    def handler(request):
        return httpx.Response(200, json=_CORRECTED_WORK)

    async with _client(handler) as client:
        result = await crossref.lookup_doi(client, "x", settings=get_settings())
    assert result.is_correction
    assert not result.retracted


async def test_old_retraction_without_structured_metadata_still_caught():
    """A real gap found live: pre-2023 retractions can carry an empty
    update-to/relation — the title-prefix fallback must still catch them."""

    def handler(request):
        return httpx.Response(200, json=_OLD_RETRACTION_NO_METADATA)

    async with _client(handler) as client:
        result = await crossref.lookup_doi(client, "x", settings=get_settings())
    assert result.retracted
    assert "RETRACTED" in result.retraction_detail


async def test_search_by_title_uses_first_result():
    def handler(request):
        assert "query.bibliographic" in str(request.url)
        return httpx.Response(200, json={"message": {"items": [_CLEAN_WORK["message"]]}})

    async with _client(handler) as client:
        result = await crossref.search_by_title(
            client, "An uncontroversial paper", settings=get_settings()
        )
    assert result.found
    assert result.title == "An uncontroversial paper"


async def test_search_by_title_no_results():
    def handler(request):
        return httpx.Response(200, json={"message": {"items": []}})

    async with _client(handler) as client:
        result = await crossref.search_by_title(
            client, "nothing matches this", settings=get_settings()
        )
    assert not result.found
