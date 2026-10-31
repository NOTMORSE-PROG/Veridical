"""F5 title search (BUG-610): which registry candidates confirm a reference.

Cases are drawn from how real theses print references and how registries
store the same works: Unicode hyphens, words split at a line end, case,
accents, particles in surnames, full display names, online-first years and
records that hold only the main title.
"""

from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.checks.citations import verify
from app.checks.citations.metadata import compare_metadata
from app.checks.citations.search_identity import search_identity
from app.checks.citations.verify import VerdictKind
from app.config import Settings
from app.errors import ApiDownError
from app.external import openalex
from app.external.schemas import VerificationResult
from app.models.citation import Citation


def settings(**overrides):
    return Settings(_env_file=None, **overrides)


def cite(title, author="Wilmers, C. C.", year=2015):
    return Citation(
        order_index=0,
        raw_text=f"{author} ({year}). {title}.",
        authors=[author],
        year=year,
        title=title,
    )


def crossref(title, family="Wilmers", years=(2015,), raw_extra=None, **patch):
    fields = ("published", "published-print", "published-online")
    raw = {"author": [{"family": family}] if family else [], **(raw_extra or {})}
    for field, year in zip(fields, years, strict=False):
        raw[field] = {"date-parts": [[year]]}
    return replace(
        VerificationResult(found=True, provider="crossref", title=title, raw=raw), **patch
    )


def openalex_work(title, name, year):
    return VerificationResult(
        found=True,
        provider="openalex",
        title=title,
        raw={
            "display_name": title,
            "publication_year": year,
            "authorships": [{"author": {"display_name": name}}],
        },
    )


def identity(citation, result, **overrides):
    config = settings(**overrides)
    comparison = compare_metadata(citation, result, settings=config)
    return search_identity(citation, result, comparison, config)


# --- the identity rule ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cited", "registered"),
    [
        (
            "The golden age of bio-logging: how animal-borne sensors are advancing",
            "The golden age of bio\u2010logging: how animal\u2010borne sensors are advancing",
        ),
        (
            "Acoustic monitoring using micro- phone arrays",
            "Acoustic monitoring using microphone arrays",
        ),
        ("ADAPTIVE THERMAL COMFORT FOR BUILDINGS", "Adaptive thermal comfort for buildings"),
        ("LashTech: a lash recommendation app", "Lash Tech: A Lash Recommendation App"),
        (
            "Energy management in a building\u2013A review",
            "Energy management in a building\u2014A review",
        ),
        (
            "\u00b5EMA: microinteraction-based assessment",
            "\u03bcEMA: Microinteraction-based assessment",
        ),
        (
            "A continental-scale tool for European bats",
            "A continental\u2010scale tool for <scp>E</scp>uropean bats",
        ),
    ],
)
def test_title_printed_differently_still_confirms(cited, registered):
    found = identity(cite(cited), crossref(registered))
    assert found is not None and found.title == "normalized"
    assert not found.strict


def test_exact_match_is_strict():
    found = identity(cite("Shazam for bats"), crossref("Shazam for bats"))
    assert found is not None and found.strict


@pytest.mark.parametrize(
    "registered",
    [
        "Shazam for birds",
        "Shazam for bats and birds",
        "Review for \u201cShazam for bats\u201d",
        "Peer Review #2 of \u201cShazam for bats (v0.1)\u201d",
    ],
)
def test_different_title_never_confirms(registered):
    assert identity(cite("Shazam for bats"), crossref(registered)) is None


@pytest.mark.parametrize(
    ("cited", "family"),
    [
        ("Castelblanco A", "Castelblanco"),
        ("van Valkenburgh, B.", "Valkenburgh"),
        ("Brandes, T. S.", "Scott Brandes"),
        ("Sepp\u00e4nen, O.", "Seppanen"),
    ],
)
def test_surname_printed_differently_still_confirms(cited, family):
    found = identity(cite("Shazam for bats", author=cited), crossref("Shazam for bats", family))
    assert found is not None and found.author in {"policy", "surname_words"}


@pytest.mark.parametrize("family", ["Jones", None, "Gradi\u0161ek"])
def test_other_or_missing_first_author_never_confirms(family):
    # "Gradiek" lost a letter in extraction; that is not the same surname.
    cited = cite("Shazam for bats", author="Gradiek, A.")
    assert identity(cited, crossref("Shazam for bats", family)) is None


@pytest.mark.parametrize(
    ("cited", "family"),
    [
        # Initials say nothing about the surname, so a record that stores
        # only an initial cannot match on it.
        ("Castelblanco A", "A"),
        # Sharing one of two surnames is not the same name.
        ("Garcia Lopez, J.", "Garcia Torres"),
    ],
)
def test_partial_name_overlap_never_confirms(cited, family):
    found = identity(cite("Shazam for bats", author=cited), crossref("Shazam for bats", family))
    assert found is None


@pytest.mark.parametrize(
    ("cited", "registered"),
    [
        ("Wei, J.", "Wei Zhang"),  # a given name, not the surname
        ("Smith J Brown K", "Brown"),  # an unsplit list of authors
        ("Miller-Struttmann, N.", "Miller"),
    ],
)
def test_given_names_and_partial_surnames_never_confirm(cited, registered):
    citation = cite("Shazam for bats", author=cited)
    assert identity(citation, openalex_work("Shazam for bats", registered, 2015)) is None
    assert identity(citation, crossref("Shazam for bats", registered)) is None


@pytest.mark.parametrize(
    ("cited", "registered"),
    [
        ("Programming in C++", "Programming in C"),
        ("Programming in C#", "Programming in C"),
        ("Bluetooth 1.5 at a glance", "Bluetooth 15 at a glance"),
    ],
)
def test_titles_differing_in_a_symbol_or_decimal_never_confirm(cited, registered):
    assert identity(cite(cited), crossref(registered)) is None


@pytest.mark.parametrize(
    ("cited", "registered", "provider", "confirmed"),
    [
        # Particles are part of a full name: these are different families.
        ("Dela Cruz, J.", "Juan Cruz", "openalex", False),
        ("Dela Cruz, J.", "Juan Dela Cruz", "openalex", True),
        # Surnames that are also on the particle list still match.
        ("Das, R.", "Rahul Das", "openalex", True),
        ("Le, T.", "Le", "crossref", True),
        ("de Dear, R.", "Richard de Dear", "openalex", True),
        # A one-word surname field may omit the particle.
        ("van Valkenburgh, B.", "Valkenburgh", "crossref", True),
    ],
)
def test_name_particles(cited, registered, provider, confirmed):
    citation = cite("Shazam for bats", author=cited)
    if provider == "openalex":
        candidate = openalex_work("Shazam for bats", registered, 2015)
    else:
        candidate = crossref("Shazam for bats", registered)
    assert (identity(citation, candidate) is not None) == confirmed


def test_openalex_full_name_confirms_by_surname():
    found = identity(
        cite("Blink Rate: A Possible Measure of Fatigue", author="Stern, J. A.", year=1994),
        openalex_work("Blink Rate: A Possible Measure of Fatigue", "John A. Stern", 1994),
    )
    assert found is not None


def test_full_name_registry_never_reports_a_differing_author():
    citation = cite("Shazam for bats", author="Jones, A.")
    comparison = compare_metadata(citation, openalex_work("Shazam for bats", "Kate Smith", 2015))
    assert "first author" not in comparison.differing_fields
    assert comparison.author_status == "unassessed"


@pytest.mark.parametrize(
    ("years", "cited_year", "expected"),
    [
        ((2006, 2007), 2007, "exact"),  # print year on the record
        ((2017,), 2018, "within_tolerance"),
        ((2017,), 2019, None),
        ((2019,), 2023, None),
        ((), 2015, None),
    ],
)
def test_year_must_be_on_the_record_or_within_tolerance(years, cited_year, expected):
    found = identity(
        cite("Heart rate variability as a biomarker", year=cited_year),
        crossref("Heart rate variability as a biomarker", years=years),
    )
    assert (found.year if found else None) == expected


def test_year_tolerance_is_configurable():
    citation = cite("Heart rate variability as a biomarker", year=2018)
    registered = crossref("Heart rate variability as a biomarker", years=(2017,))
    assert identity(citation, registered, citation_title_search_year_tolerance=0) is None


def test_reference_giving_only_the_main_title_confirms_with_exact_year():
    cited = "What drives the adoption of travel apps?"
    registered = crossref("What Drives the Adoption of Travel Apps? A study")
    assert identity(cite(cited), registered).title == "main_title"
    assert identity(cite(cited, year=2016), registered) is None


def test_cited_subtitle_the_record_lacks_never_confirms():
    # Nothing on the record shows the subtitle is right, so an invented one
    # must not pass.
    cited = cite("Micro-Atmospheres: Exploring portable solutions for comfort")
    assert identity(cited, crossref("Micro-Atmospheres")) is None


def test_crossref_subtitle_field_is_part_of_the_title():
    registered = crossref(
        "Micro-Atmospheres", raw_extra={"subtitle": ["Exploring portable solutions for comfort"]}
    )
    found = identity(
        cite("Micro-Atmospheres: Exploring portable solutions for comfort"), registered
    )
    assert found is not None and found.title == "normalized"
    other = cite("Micro-Atmospheres: Exploring wearable solutions for comfort")
    assert identity(other, registered) is None


def test_main_title_needs_a_separator_not_a_prefix():
    assert identity(cite("Deep learning for bats"), crossref("Deep learning")) is None


@pytest.mark.parametrize("flag", ["retracted", "is_correction"])
def test_loose_match_never_carries_a_retraction_or_correction(flag):
    registered = crossref("ADAPTIVE THERMAL COMFORT", **{flag: True})
    assert identity(cite("Adaptive thermal comfort"), registered) is None
    assert identity(cite("ADAPTIVE THERMAL COMFORT"), registered).strict


def test_no_cited_year_never_confirms():
    citation = cite("Shazam for bats")
    citation.year = None
    assert identity(citation, crossref("Shazam for bats")) is None


# --- asking the registries --------------------------------------------------------------


def no_cache(monkeypatch, cached=None):
    monkeypatch.setattr(verify.cache, "get_cached", AsyncMock(return_value=cached))
    monkeypatch.setattr(verify.cache, "store_result", AsyncMock())
    monkeypatch.setattr(verify.cache, "is_instructor_confirmed", AsyncMock(return_value=False))


async def test_a_later_candidate_confirms_when_the_first_is_a_review_record(monkeypatch):
    no_cache(monkeypatch)
    wanted = crossref("Shazam for bats")
    monkeypatch.setattr(
        verify.crossref_client,
        "search_title_candidates",
        AsyncMock(return_value=[crossref("Review for Shazam for bats", None), wanted]),
    )
    other = AsyncMock(return_value=[])
    monkeypatch.setattr(verify.openalex_client, "search_title_candidates", other)
    out = await verify.verify_citation(None, None, cite("Shazam for bats"), settings=settings())
    assert out.kind == VerdictKind.existence_confirmed
    assert out.result.title == "Shazam for bats"
    assert out.search_identity.strict
    other.assert_not_called()
    # Crossref is asked with the citation's surname and year, not the title alone.
    query = verify.crossref_client.search_title_candidates.await_args.args[1]
    assert query == "Shazam for bats Wilmers 2015"


async def test_the_closest_candidate_wins_over_a_higher_ranked_loose_one(monkeypatch):
    # A preprint cited with its own year is the cited record, even when the
    # journal version a year later ranks first.
    no_cache(monkeypatch)
    journal = crossref("Robust monitoring of ecosystems", "Sethi", (2018,))
    preprint = crossref("Robust monitoring of ecosystems", "Sethi", (2017,))
    monkeypatch.setattr(
        verify.crossref_client,
        "search_title_candidates",
        AsyncMock(return_value=[journal, preprint]),
    )
    out = await verify.verify_citation(
        None,
        None,
        cite("Robust monitoring of ecosystems", author="Sethi, S. S.", year=2017),
        settings=settings(),
    )
    assert out.result.raw == preprint.raw
    assert out.search_identity.strict


async def test_openalex_confirms_what_crossref_does_not_hold(monkeypatch):
    no_cache(monkeypatch)
    monkeypatch.setattr(
        verify.crossref_client,
        "search_title_candidates",
        AsyncMock(return_value=[crossref("Some other paper", "Sage", (2021,))]),
    )
    preprint = openalex_work("Key information extraction from documents", "Oliver Bensch", 2021)
    monkeypatch.setattr(
        verify.openalex_client, "search_title_candidates", AsyncMock(return_value=[preprint])
    )
    out = await verify.verify_citation(
        None,
        None,
        cite("Key information extraction from documents", author="Bensch, O.", year=2021),
        settings=settings(),
    )
    assert out.kind == VerdictKind.existence_confirmed
    assert out.result.provider == "openalex"
    assert out.result.attempted_providers == ["crossref", "openalex"]
    assert verify._verdict_flag_draft(cite("x"), out) is None


def rate_limited():
    request = httpx.Request("GET", "https://api.openalex.org/works")
    cause = httpx.HTTPStatusError(
        "429", request=request, response=httpx.Response(429, request=request)
    )
    error = ApiDownError("openalex lookup could not be completed")
    error.__cause__ = cause
    return error


async def run_searches(monkeypatch, failure, references):
    no_cache(monkeypatch)
    store = AsyncMock()
    monkeypatch.setattr(verify.cache, "store_result", store)
    monkeypatch.setattr(
        verify.crossref_client,
        "search_title_candidates",
        AsyncMock(return_value=[crossref("Some other paper", "Sage", (2021,))]),
    )
    failing = AsyncMock(side_effect=failure)
    monkeypatch.setattr(verify.openalex_client, "search_title_candidates", failing)
    run = verify.TitleSearchRun()
    verdicts = [
        await verify.verify_citation(
            None, None, cite(f"Shazam for bats {n}"), settings=settings(), title_search_run=run
        )
        for n in range(references)
    ]
    return verdicts, run, failing, store


async def test_rate_limited_registry_is_skipped_for_the_rest_of_the_run(monkeypatch):
    verdicts, run, failing, store = await run_searches(monkeypatch, rate_limited(), 3)
    assert failing.await_count == 1
    assert run.skipped == {"openalex"}
    for out in verdicts:
        # Unconfirmed, and saying a registry was not asked, never "not found".
        assert out.kind == VerdictKind.unassessed
        assert out.unassessed_reason == "title_search_registry_unavailable"
    assert store.await_args.kwargs["result"].attempted_providers == ["crossref"]


async def test_one_brief_failure_costs_only_that_reference(monkeypatch):
    request = httpx.Request("GET", "https://api.openalex.org/works")
    flaky = [httpx.ConnectError("blip", request=request), [], []]
    verdicts, run, failing, _ = await run_searches(monkeypatch, flaky, 3)
    assert failing.await_count == 3
    assert run.skipped == set()
    assert [out.unassessed_reason for out in verdicts] == [
        "title_search_registry_unavailable",
        "title_search_identity_unconfirmed",
        "title_search_identity_unconfirmed",
    ]


async def test_repeated_failures_in_a_row_skip_the_registry(monkeypatch):
    request = httpx.Request("GET", "https://api.openalex.org/works")
    verdicts, run, failing, _ = await run_searches(
        monkeypatch, httpx.ConnectError("down", request=request), 5
    )
    limit = settings().citation_title_search_failures_before_skip
    assert failing.await_count == limit
    assert run.skipped == {"openalex"}


async def test_a_crossref_throttle_is_not_a_spent_budget(monkeypatch):
    # Crossref has no daily budget: one 429 costs that reference only.
    no_cache(monkeypatch)
    request = httpx.Request("GET", "https://api.crossref.org/works")
    throttled = ApiDownError("crossref lookup could not be completed")
    throttled.__cause__ = httpx.HTTPStatusError(
        "429", request=request, response=httpx.Response(429, request=request)
    )
    crossref_search = AsyncMock(side_effect=[throttled, [crossref("Shazam for bats 1")]])
    monkeypatch.setattr(verify.crossref_client, "search_title_candidates", crossref_search)
    monkeypatch.setattr(
        verify.openalex_client, "search_title_candidates", AsyncMock(return_value=[])
    )
    run = verify.TitleSearchRun()
    for n in range(2):
        out = await verify.verify_citation(
            None, None, cite(f"Shazam for bats {n}"), settings=settings(), title_search_run=run
        )
    assert run.skipped == set()
    assert out.kind == VerdictKind.existence_confirmed


async def test_failures_separated_by_an_answer_do_not_add_up(monkeypatch):
    request = httpx.Request("GET", "https://api.openalex.org/works")
    blip = httpx.ConnectError("blip", request=request)
    _, run, failing, _ = await run_searches(monkeypatch, [blip, blip, [], blip, blip], 5)
    assert failing.await_count == 5
    assert run.skipped == set()


async def test_a_crossref_blip_does_not_mark_the_run_down(monkeypatch):
    no_cache(monkeypatch)
    request = httpx.Request("GET", "https://api.crossref.org/works")
    crossref_search = AsyncMock(
        side_effect=[httpx.ConnectError("blip", request=request), [crossref("Shazam for bats 1")]]
    )
    monkeypatch.setattr(verify.crossref_client, "search_title_candidates", crossref_search)
    monkeypatch.setattr(
        verify.openalex_client, "search_title_candidates", AsyncMock(return_value=[])
    )
    run = verify.TitleSearchRun()
    first = await verify.verify_citation(
        None, None, cite("Shazam for bats 0"), settings=settings(), title_search_run=run
    )
    second = await verify.verify_citation(
        None, None, cite("Shazam for bats 1"), settings=settings(), title_search_run=run
    )
    assert first.kind == VerdictKind.api_down
    assert second.kind == VerdictKind.existence_confirmed


async def test_no_candidate_anywhere_is_not_found(monkeypatch):
    no_cache(monkeypatch)
    monkeypatch.setattr(
        verify.crossref_client, "search_title_candidates", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        verify.openalex_client, "search_title_candidates", AsyncMock(return_value=[])
    )
    out = await verify.verify_citation(None, None, cite("Shazam for bats"), settings=settings())
    assert out.kind == VerdictKind.not_found
    assert out.result.attempted_providers == ["crossref", "openalex"]


async def test_no_registry_answering_is_api_down(monkeypatch):
    no_cache(monkeypatch)
    down = AsyncMock(side_effect=httpx.ConnectError("offline"))
    monkeypatch.setattr(verify.crossref_client, "search_title_candidates", down)
    monkeypatch.setattr(verify.openalex_client, "search_title_candidates", down)
    out = await verify.verify_citation(None, None, cite("Shazam for bats"), settings=settings())
    assert out.kind == VerdictKind.api_down


THIS_REFERENCE = "shazam for bats|wilmers|2015"


@pytest.mark.parametrize(
    ("attempted", "query_key", "refetched"),
    [
        ([], THIS_REFERENCE, True),
        (["crossref"], THIS_REFERENCE, True),
        (["crossref", "openalex"], THIS_REFERENCE, False),
        # Stored for another reference with the same title (or before this
        # change): asked again for this one.
        (["crossref", "openalex"], "shazam for bats|aodha|2016", True),
        (["crossref", "openalex"], None, True),
    ],
)
async def test_cached_miss_is_reused_only_for_the_same_complete_search(
    monkeypatch, attempted, query_key, refetched
):
    stale = crossref(
        "Some other paper", "Sage", (2021,), attempted_providers=attempted, query_key=query_key
    )
    no_cache(monkeypatch, cached=stale)
    fetch = AsyncMock(return_value=[crossref("Shazam for bats")])
    monkeypatch.setattr(verify.crossref_client, "search_title_candidates", fetch)
    monkeypatch.setattr(
        verify.openalex_client, "search_title_candidates", AsyncMock(return_value=[])
    )
    out = await verify.verify_citation(None, None, cite("Shazam for bats"), settings=settings())
    assert fetch.await_count == (1 if refetched else 0)
    assert out.kind == (VerdictKind.existence_confirmed if refetched else VerdictKind.unassessed)


async def test_cached_confirmed_candidate_is_reused(monkeypatch):
    no_cache(monkeypatch, cached=crossref("Shazam for bats"))
    fetch = AsyncMock()
    monkeypatch.setattr(verify.crossref_client, "search_title_candidates", fetch)
    out = await verify.verify_citation(None, None, cite("Shazam for bats"), settings=settings())
    assert out.kind == VerdictKind.existence_confirmed
    fetch.assert_not_called()


# --- the OpenAlex client ----------------------------------------------------------------


async def test_openalex_request_shape_and_parsing():
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        seen["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "doi": "https://doi.org/10.1177/001872089403600209",
                        "display_name": "Blink Rate: A Possible Measure of Fatigue",
                        "publication_year": 1994,
                        "authorships": [{"author": {"display_name": "John A. Stern"}}],
                    },
                    "not a record",
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await openalex.search_title_candidates(
            client, "Blink Rate: A, Possible | Measure", settings=settings(), rows=5
        )
    assert seen["params"]["filter"] == "title.search:Blink Rate A Possible Measure"
    assert seen["params"]["per_page"] == "5"
    assert "api_key" not in seen["params"] and "mailto" not in seen["params"]
    assert "mailto" not in seen["headers"].get("user-agent", "")
    assert [r.title for r in results] == ["Blink Rate: A Possible Measure of Fatigue"]
    assert results[0].provider == "openalex" and not results[0].retracted


async def test_openalex_sends_a_configured_key():
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await openalex.search_title_candidates(
            client, "Anything", settings=settings(openalex_api_key="free-key"), rows=5
        )
    assert results == [] and seen["params"]["api_key"] == "free-key"
