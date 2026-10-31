"""F5: a provider search candidate is not identity-bound citation evidence."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.checks.citations import verify
from app.checks.citations.verify import VerdictKind
from app.config import Settings
from app.external.schemas import VerificationResult
from app.models.citation import Citation


def citation():
    return Citation(
        order_index=0,
        raw_text="Smith, A. (2021). A domain independent study.",
        authors=["Smith, A."],
        year=2021,
        title="A domain independent study",
    )


def candidate(**patch):
    base = VerificationResult(
        found=True,
        provider="crossref",
        title="A domain independent study",
        raw={"author": [{"family": "Smith"}], "published": {"date-parts": [[2021]]}},
    )
    return replace(base, **patch)


def every_registry(value):
    """A cached search for this reference that asked every configured
    registry is complete."""
    providers = Settings(_env_file=None).citation_title_search_providers
    return replace(
        value,
        attempted_providers=list(providers),
        query_key="a domain independent study|smith|2021",
    )


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize(
    "wrong", ["title", "author", "year", "missingauthor", "missingyear", "retracted", "corrected"]
)
async def test_unbound_first_hit_never_confirms_or_transfers_flags(monkeypatch, cached, wrong):
    value = candidate()
    if wrong == "title":
        value = replace(value, title="Completely unrelated provider work")
    if wrong in ("author", "missingauthor", "year", "missingyear"):
        raw = dict(value.raw)
        if wrong == "author":
            raw["author"] = [{"family": "Jones"}]
        if wrong == "missingauthor":
            raw.pop("author")
        if wrong == "year":
            raw["published"] = {"date-parts": [[1999]]}
        if wrong == "missingyear":
            raw.pop("published")
        value = replace(value, raw=raw)
    if wrong in ("retracted", "corrected"):
        value = replace(
            value,
            title="Unrelated provider work",
            retracted=wrong == "retracted",
            is_correction=wrong == "corrected",
        )
    if cached:
        value = every_registry(value)
    monkeypatch.setattr(
        verify.cache, "get_cached", AsyncMock(return_value=value if cached else None)
    )
    store = AsyncMock()
    monkeypatch.setattr(verify.cache, "store_result", store)
    fetch = AsyncMock(return_value=[value])
    monkeypatch.setattr(verify.crossref_client, "search_title_candidates", fetch)
    monkeypatch.setattr(
        verify.openalex_client, "search_title_candidates", AsyncMock(return_value=[])
    )
    row = citation()
    out = await verify.verify_citation(None, None, row, settings=Settings(_env_file=None))
    assert out.kind == VerdictKind.unassessed
    assert out.unassessed_reason == "title_search_identity_unconfirmed"
    assert out.metadata_comparison and out.result.title == value.title
    assert verify._verdict_flag_draft(row, out) is None
    assert fetch.await_count == (0 if cached else 1)


@pytest.mark.parametrize("retracted", [False, True])
async def test_known_bound_search_record_keeps_its_actual_status(monkeypatch, retracted):
    monkeypatch.setattr(
        verify.cache, "get_cached", AsyncMock(return_value=candidate(retracted=retracted))
    )
    out = await verify.verify_citation(None, None, citation(), settings=Settings(_env_file=None))
    assert out.kind == (VerdictKind.retracted if retracted else VerdictKind.existence_confirmed)
    assert out.metadata_comparison.title_status == "agrees"


@pytest.mark.parametrize("matched", [False, True])
async def test_cached_google_title_fallback_requires_returned_requested_isbn(monkeypatch, matched):
    row = citation()
    row.isbn = "978-1-4028-9462-6"
    result = VerificationResult(
        found=True,
        provider="gbooks",
        title="Potential title candidate",
        content_checkable=False,
        raw={
            "volumeInfo": {
                "industryIdentifiers": [
                    {
                        "type": "ISBN_13",
                        "identifier": "9781402894626" if matched else "9789999999999",
                    }
                ]
            }
        },
    )
    monkeypatch.setattr(verify.cache, "get_cached", AsyncMock(return_value=result))
    out = await verify.verify_citation(None, None, row, settings=Settings(_env_file=None))
    assert out.kind == (VerdictKind.existence_confirmed if matched else VerdictKind.unassessed)
    if not matched:
        assert out.unassessed_reason == "book_search_identity_unconfirmed"
        assert verify._verdict_flag_draft(row, out) is None


async def test_title_policy_missing_withholds_before_any_lookup(monkeypatch, tmp_path):
    fetch = AsyncMock()
    monkeypatch.setattr(verify.crossref_client, "search_title_candidates", fetch)
    out = await verify.verify_citation(
        None,
        None,
        citation(),
        settings=Settings(
            _env_file=None, citation_metadata_title_policy_file=tmp_path / "missing-policy.json"
        ),
    )
    assert out.kind == VerdictKind.unassessed
    fetch.assert_not_called()


async def test_title_only_s2_record_without_author_year_identity_stays_unassessed(monkeypatch):
    monkeypatch.setattr(
        verify.cache,
        "get_cached",
        AsyncMock(return_value=every_registry(candidate(provider="s2", raw={}))),
    )
    out = await verify.verify_citation(None, None, citation(), settings=Settings(_env_file=None))
    assert out.kind == VerdictKind.unassessed


@pytest.mark.parametrize(
    "raw", [None, [], {"volumeInfo": None}, {"volumeInfo": {"industryIdentifiers": None}}]
)
async def test_malformed_cached_book_candidate_is_unassessed(monkeypatch, raw):
    row = citation()
    row.isbn = "9781402894626"
    monkeypatch.setattr(
        verify.cache, "get_cached", AsyncMock(return_value=candidate(provider="gbooks", raw=raw))
    )
    out = await verify.verify_citation(None, None, row, settings=Settings(_env_file=None))
    assert out.kind == VerdictKind.unassessed
    assert verify._verdict_flag_draft(row, out) is None
