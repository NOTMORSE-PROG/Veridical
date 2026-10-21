"""F5 exact DOI provider coverage; no network or database required."""

import httpx
import pytest

from app.checks.citations.metadata import metadata_mismatches
from app.checks.citations.verify import (
    VerdictKind,
    _fetch_supplementary_abstract,
    _verdict_flag_draft,
    verify_citation,
)
from app.config import Settings
from app.external import cache
from app.external.schemas import VerificationResult
from app.models.citation import Citation
from app.models.enums import CitationParseStatus

DOI = "10.5281/zenodo.1450596"


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    async def empty(*args, **kwargs):
        return None

    async def false(*args, **kwargs):
        return False

    monkeypatch.setattr(cache, "get_cached", empty)
    monkeypatch.setattr(cache, "store_result", empty)
    monkeypatch.setattr(cache, "is_instructor_confirmed", false)


def citation(title="A source", year=2018):
    return Citation(
        order_index=0,
        raw_text=f"Smith, J. ({year}). {title}. doi:{DOI}",
        authors=["Smith, J."],
        title=title,
        year=year,
        doi=DOI,
        parse_status=CitationParseStatus.parsed,
    )


def datacite_record(doi=DOI, title="A source", year=2018):
    return {
        "data": {
            "id": doi,
            "type": "dois",
            "attributes": {
                "doi": doi,
                "titles": [{"title": title}],
                "publicationYear": year,
                "creators": [{"name": "Smith, J.", "familyName": "Smith"}],
                "url": "https://example.org/source",
            },
        }
    }


@pytest.mark.asyncio
async def test_crossref_miss_uses_exact_datacite_identifier_and_keeps_support_unassessed():
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(404)
            if request.url.host == "api.crossref.org"
            else httpx.Response(200, json=datacite_record())
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verdict = await verify_citation(None, client, citation(), settings=Settings())
    assert verdict.kind == VerdictKind.existence_confirmed
    assert verdict.result.provider == "datacite"
    assert verdict.result.attempted_providers == ["crossref", "datacite"]
    assert verdict.result.abstract is None and not verdict.result.content_checkable
    assert len(calls) == 2 and all("query" not in request.url.params for request in calls)
    assert _verdict_flag_draft(citation(), verdict) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["wrong_doi", "missing_doi", "wrong_title", "wrong_year"])
async def test_unrelated_or_conflicting_metadata_never_silently_confirms(wrong):
    record = {"DOI": DOI, "title": ["A source"], "published": {"date-parts": [[2018]]}}
    if wrong == "wrong_doi":
        record["DOI"] = "10.9999/unrelated"
    if wrong == "missing_doi":
        record.pop("DOI")
    if wrong == "wrong_title":
        record["title"] = ["A different publication"]
    if wrong == "wrong_year":
        record["published"] = {"date-parts": [[2022]]}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"message": record}))
    ) as client:
        verdict = await verify_citation(None, client, citation(), settings=Settings())
    assert verdict.kind == (
        VerdictKind.unassessed
        if wrong in ("wrong_doi", "missing_doi")
        else VerdictKind.metadata_mismatch
    )


@pytest.mark.asyncio
async def test_both_registry_misses_record_only_actual_providers():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    ) as client:
        verdict = await verify_citation(None, client, citation(), settings=Settings())
    assert verdict.kind == VerdictKind.not_found
    draft = _verdict_flag_draft(citation(), verdict)
    assert draft.detail["providers_attempted"] == ["crossref", "datacite"]
    assert "Semantic Scholar" not in draft.detail["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [429, 503, "nonjson", "schema", "timeout"])
async def test_unavailable_registry_is_not_a_not_found_result(failure):
    def handler(request):
        if request.url.host == "api.crossref.org":
            return httpx.Response(404)
        if failure == "timeout":
            raise httpx.ReadTimeout("controlled timeout", request=request)
        if failure == "nonjson":
            return httpx.Response(200, text="invalid")
        if failure == "schema":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(failure)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verdict = await verify_citation(
            None, client, citation(), settings=Settings(external_max_retries=0)
        )
    assert verdict.kind == VerdictKind.api_down


@pytest.mark.asyncio
async def test_legacy_crossref_negative_does_not_hide_new_provider_coverage(monkeypatch):
    async def old(*args, **kwargs):
        return VerificationResult(found=False, provider="crossref")

    monkeypatch.setattr(cache, "get_cached", old)
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return (
            httpx.Response(404)
            if request.url.host == "api.crossref.org"
            else httpx.Response(200, json=datacite_record())
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verdict = await verify_citation(None, client, citation(), settings=Settings())
    assert verdict.kind == VerdictKind.existence_confirmed
    assert calls == ["api.crossref.org", "api.datacite.org"]


@pytest.mark.parametrize(
    "provider,raw",
    [
        ("crossref", {"published": None, "author": None}),
        ("crossref", {"published": {"date-parts": [None]}, "author": ["Smith"]}),
        ("datacite", {"data": {"attributes": {"creators": None}}}),
        ("datacite", {"data": {"attributes": {"creators": ["Smith"]}}}),
        ("datacite", {"data": None}),
    ],
)
def test_absent_or_malformed_optional_metadata_cannot_crash_or_invent_a_difference(provider, raw):
    result = VerificationResult(found=True, provider=provider, title="A source", raw=raw)
    assert metadata_mismatches(citation(), result) == []


@pytest.mark.parametrize("name", ["Smith, John", "John Smith"])
def test_full_creator_name_without_family_decomposition_is_not_a_surname_conflict(name):
    raw = datacite_record()
    raw["data"]["attributes"]["creators"] = [{"name": name, "nameType": "Personal"}]
    result = VerificationResult(found=True, provider="datacite", title="A source", raw=raw)
    assert metadata_mismatches(citation(), result) == []
    raw["data"]["attributes"]["creators"] = [{"familyName": "Jones"}]
    assert metadata_mismatches(citation(), result) == ["first author"]


@pytest.mark.asyncio
async def test_fresh_negative_abstract_cache_does_not_repeat_provider_calls(monkeypatch):
    async def negative(*args, **kwargs):
        return VerificationResult(found=False, provider="s2")

    monkeypatch.setattr(cache, "get_cached", negative)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("a fresh negative abstract lookup must be reused")
        )
    ) as client:
        for _ in range(2):
            assert (
                await _fetch_supplementary_abstract(None, client, DOI, settings=Settings()) is None
            )


@pytest.mark.asyncio
async def test_malformed_crossref_relation_is_unavailable_not_absence():
    def handler(request):
        if request.url.host == "api.crossref.org":
            return httpx.Response(200, json={"message": {"DOI": DOI, "updated-by": ["bad"]}})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verdict = await verify_citation(None, client, citation(), settings=Settings())
    assert verdict.kind == VerdictKind.api_down


@pytest.mark.asyncio
async def test_malformed_supplementary_metadata_does_not_destroy_exact_existence():
    def handler(request):
        if request.url.host == "api.crossref.org":
            return httpx.Response(200, json={"message": {"DOI": DOI, "title": ["A source"]}})
        return httpx.Response(
            200,
            json={"title": "A source", "externalIds": {"DOI": DOI}, "openAccessPdf": ["invalid"]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verdict = await verify_citation(None, client, citation(), settings=Settings())
    assert verdict.kind == VerdictKind.existence_confirmed
    assert verdict.result.abstract is None
