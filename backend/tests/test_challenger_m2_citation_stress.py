"""Adversarial challenger test suite for Milestone 2 Citation Integrity fixes.

Empirically tests and challenges:
1. Spaced n. d. date notation in reference entries and in-text parenthetical/narrative citations.
2. Corporate authors joined by and/& without commas.
3. Dotted acronym normalization in author aliases and cross-matching.
4. DataCite corrupt, malformed, non-dict schema response, and network timeout handling.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.checks.citations.cross_match import cross_match, get_author_aliases
from app.checks.citations.extract import _split_authors, extract_in_text_citations
from app.checks.citations.verify import VerdictKind, verify_citation
from app.config import Settings, get_settings
from app.errors import ApiDownError
from app.external import cache
from app.external import datacite as datacite_client
from app.ingest.references import parse_reference
from app.ingest.schemas import TextBlock
from app.models.citation import Citation
from app.models.enums import CitationParseStatus


@dataclass
class MockRef:
    order_index: int
    raw_text: str
    authors: list[str] | None
    year: int | str | None


@dataclass(frozen=True)
class MockInText:
    surnames: list[str]
    year: str
    is_narrative: bool = False
    is_secondary: bool = False
    raw_text: str = ""
    anchor: str = "p. 1"
    page: int | None = 1
    paragraph: int | None = 1
    claim_sentence: str = ""
    claim_excerpt_cropped: bool = False


def _block(text: str, paragraph: int = 1) -> TextBlock:
    return TextBlock(page=1, paragraph=paragraph, text=text, max_font_size=11, bold_ratio=0)


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    async def empty(*args, **kwargs):
        return None

    async def false(*args, **kwargs):
        return False

    monkeypatch.setattr(cache, "get_cached", empty)
    monkeypatch.setattr(cache, "store_result", empty)
    monkeypatch.setattr(cache, "is_instructor_confirmed", false)


# =============================================================================
# 1. Spaced n. d. / (n. d.) Date Notation Stress Tests
# =============================================================================


@pytest.mark.parametrize(
    "raw_ref, expected_author, expected_year",
    [
        (
            "Smith, J. (n. d.). Algorithmic foundations. Academic Press.",
            ["Smith, J."],
            "n.d.",
        ),
        (
            "Smith, J. (n.  d.). Algorithmic foundations. Academic Press.",
            ["Smith, J."],
            "n.d.",
        ),
        (
            "Smith, J. (n.   d.). Algorithmic foundations. Academic Press.",
            ["Smith, J."],
            "n.d.",
        ),
        (
            "Smith, J. (N. D.). Algorithmic foundations. Academic Press.",
            ["Smith, J."],
            "n.d.",
        ),
        (
            "Smith, J. (N. d.). Algorithmic foundations. Academic Press.",
            ["Smith, J."],
            "n.d.",
        ),
        (
            "Smith, J. (n. D.). Algorithmic foundations. Academic Press.",
            ["Smith, J."],
            "n.d.",
        ),
        (
            "Smith, J. (n. d.a). Algorithmic foundations. Academic Press.",
            ["Smith, J."],
            "n.d.",
        ),
        (
            "Smith, J. (n. d., May 5). Algorithmic foundations. Academic Press.",
            ["Smith, J."],
            "n.d.",
        ),
        (
            "[1] Smith, J. (n. d.). Algorithmic foundations. Academic Press.",
            ["Smith, J."],
            "n.d.",
        ),
        (
            "1. Smith, J. (n. d.). Algorithmic foundations. Academic Press.",
            ["Smith, J."],
            "n.d.",
        ),
        (
            "World Health Organization. (n. d.). Guidance note. WHO.",
            ["World Health Organization"],
            "n.d.",
        ),
    ],
)
def test_reference_spaced_nd_variations_passing(raw_ref, expected_author, expected_year):
    """Verify single- and multi-spaced n. d. variations in reference list parsing."""
    draft = parse_reference(raw_ref, 0)
    assert draft.parse_status == CitationParseStatus.parsed, (
        f"Failed parsing reference: status={draft.parse_status}, raw={raw_ref}"
    )
    assert draft.year == expected_year
    assert draft.authors == expected_author


@pytest.mark.parametrize(
    "text, expected_surname, expected_year, expected_narrative",
    [
        ("Prior research shows (Smith, n. d.).", ["Smith"], "n.d.", False),
        ("Prior research shows (Smith, n.  d.).", ["Smith"], "n.d.", False),
        ("Prior research shows (Smith, N. D.).", ["Smith"], "n.d.", False),
        ("Prior research shows (Smith, N. d.).", ["Smith"], "n.d.", False),
        ("Prior research shows (Smith, n. d., p. 45).", ["Smith"], "n.d.", False),
        ("Prior research shows (Smith, n. d., pp. 10-15).", ["Smith"], "n.d.", False),
        ("Prior research shows (Smith, n. d., para. 3).", ["Smith"], "n.d.", False),
        ("Prior research shows (Smith & Jones, n. d.).", ["Smith", "Jones"], "n.d.", False),
        (
            "Prior research shows (Smith, Jones, & Taylor, n. d.).",
            ["Smith", "Jones", "Taylor"],
            "n.d.",
            False,
        ),
        ("Smith (n. d.) noted that algorithmic bias exists.", ["Smith"], "n.d.", True),
        ("Smith (n.  d.) noted that algorithmic bias exists.", ["Smith"], "n.d.", True),
        ("Smith (N. D.) noted that algorithmic bias exists.", ["Smith"], "n.d.", True),
        ("Smith (N. d.) noted that algorithmic bias exists.", ["Smith"], "n.d.", True),
        (
            "World Health Organization (n. d.) published clinical guidance.",
            ["World Health Organization"],
            "n.d.",
            True,
        ),
    ],
)
def test_in_text_spaced_nd_variations_passing(
    text, expected_surname, expected_year, expected_narrative
):
    """Verify single- and multi-space n. d. extraction for in-text citations."""
    cites = extract_in_text_citations([_block(text)])
    assert len(cites) >= 1, f"Failed to extract citation from text: '{text}'"
    cite = cites[0]
    assert cite.year == expected_year
    assert cite.surnames == expected_surname
    assert cite.is_narrative is expected_narrative


def test_in_text_multiple_years_with_spaced_nd():
    """Verify author with multiple works including spaced n. d. in one citation."""
    text = "Several works discuss this (Smith, 2020, n. d.)."
    cites = extract_in_text_citations([_block(text)])
    assert len(cites) == 2, f"Expected 2 citations for (Smith, 2020, n. d.), found {len(cites)}"
    assert cites[0].year == "2020"
    assert cites[0].surnames == ["Smith"]
    assert cites[1].year == "n.d."
    assert cites[1].surnames == ["Smith"]


def test_in_text_semicolon_list_with_spaced_nd():
    """Verify semicolon-delimited parenthetical list containing spaced n. d."""
    text = "Standard practices (Smith, n. d.; Doe, 2021; Garcia, n. d.)."
    cites = extract_in_text_citations([_block(text)])
    assert len(cites) == 3, f"Expected 3 citations, found {len(cites)}"
    assert [c.year for c in cites] == ["n.d.", "2021", "n.d."]
    assert [c.surnames for c in cites] == [["Smith"], ["Doe"], ["Garcia"]]


@pytest.mark.parametrize(
    "in_text_year, ref_year",
    [
        ("n.d.", "n.d."),
        ("n. d.", "n.d."),
        ("n.d.", "n. d."),
        ("N. D.", "n.d."),
        ("n.d.", "N. D."),
    ],
)
def test_cross_match_nd_matrix(in_text_year, ref_year):
    """Verify cross_match reliably links in-text citation and reference across n.d. variations."""
    ref_raw = f"Smith, J. ({ref_year}). Foundations of Software Engineering."
    parsed_ref = parse_reference(ref_raw, 0)
    in_text = MockInText(surnames=["Smith"], year="n.d.", raw_text=f"(Smith, {in_text_year})")

    res = cross_match([in_text], [parsed_ref])
    assert len(res.linked) == 1, (
        f"Failed linking in-text ({in_text_year}) to ref ({ref_year}): "
        f"linked={len(res.linked)}, orphans={len(res.orphans)}"
    )
    assert len(res.orphans) == 0


# =============================================================================
# 2. Corporate Authors Joined by "and" / "&" Without Commas
# =============================================================================


@pytest.mark.parametrize(
    "raw_input, expected_split",
    [
        (
            "Food and Agriculture Organization and World Health Organization",
            ["Food and Agriculture Organization", "World Health Organization"],
        ),
        (
            "Food and Agriculture Organization & World Health Organization",
            ["Food and Agriculture Organization", "World Health Organization"],
        ),
        (
            "Philippine Statistics Authority and Department of Information "
            "and Communications Technology",
            [
                "Philippine Statistics Authority",
                "Department of Information and Communications Technology",
            ],
        ),
        (
            "Department of Health and Department of Education",
            ["Department of Health", "Department of Education"],
        ),
        (
            "Department of Health & Department of Education",
            ["Department of Health", "Department of Education"],
        ),
        (
            "Commission on Higher Education and Department of Education",
            ["Commission on Higher Education", "Department of Education"],
        ),
        (
            "Department of Science and Technology and Commission on Higher Education",
            [
                "Department of Science and Technology",
                "Commission on Higher Education",
            ],
        ),
        (
            "Food and Agriculture Organization and World Health Organization "
            "and International Atomic Energy Agency",
            [
                "Food and Agriculture Organization",
                "World Health Organization",
                "International Atomic Energy Agency",
            ],
        ),
        (
            "Food and Agriculture Organization & World Health Organization "
            "& International Atomic Energy Agency",
            [
                "Food and Agriculture Organization",
                "World Health Organization",
                "International Atomic Energy Agency",
            ],
        ),
        (
            "World Health Organization & Department of Health",
            ["World Health Organization", "Department of Health"],
        ),
    ],
)
def test_corporate_authors_splitting_passing(raw_input, expected_split):
    """Verify corporate author splitting for supported multi-entity corporate names."""
    splits = _split_authors(raw_input, reference_authors=expected_split)
    assert splits == expected_split, f"Failed splitting '{raw_input}': got {splits}"


@pytest.mark.parametrize(
    "raw_input, expected_split",
    [
        (
            "Department of Science and Technology & World Health Organization",
            ["Department of Science and Technology", "World Health Organization"],
        ),
        (
            "Department of Science and Technology and World Health Organization",
            ["Department of Science and Technology", "World Health Organization"],
        ),
        (
            "Department of Science and Technology & Philippine Statistics Authority",
            ["Department of Science and Technology", "Philippine Statistics Authority"],
        ),
        (
            "Department of Environment and Natural Resources and World Health Organization",
            ["Department of Environment and Natural Resources", "World Health Organization"],
        ),
    ],
)
def test_corporate_authors_internal_conjunction_corruption_bug(raw_input, expected_split):
    """Adversarial stress test demonstrating critical bug: internal 'and' is improperly split."""
    splits = _split_authors(raw_input, reference_authors=expected_split)
    assert splits == expected_split, f"Failed splitting '{raw_input}': got {splits}"


@pytest.mark.parametrize(
    "raw_input, expected_split",
    [
        (
            "National Institutes of Health and World Health Organization",
            ["National Institutes of Health", "World Health Organization"],
        ),
        (
            "Centers for Disease Control and Prevention and World Health Organization",
            ["Centers for Disease Control and Prevention", "World Health Organization"],
        ),
    ],
)
def test_corporate_authors_plural_institutional_words_bug(raw_input, expected_split):
    """Adversarial stress test demonstrating bug: plural corporate nouns fail to split."""
    splits = _split_authors(raw_input, reference_authors=expected_split)
    assert splits == expected_split, f"Failed splitting '{raw_input}': got {splits}"


def test_cross_match_corrupted_corporate_author_fails_linking():
    """Downstream bug: name corruption prevents cross_match from linking in-text to reference."""
    ref = MockRef(
        0,
        "Department of Science and Technology & World Health Organization. (2024). Health Report.",
        ["Department of Science and Technology", "World Health Organization"],
        2024,
    )
    raw_cite = "Department of Science and Technology & World Health Organization"
    surnames = _split_authors(raw_cite)
    cite = MockInText(
        surnames=surnames,
        year="2024",
        raw_text=f"({raw_cite}, 2024)",
    )
    res = cross_match([cite], [ref])
    assert len(res.linked) == 1, (
        f"Failed linking joint corporate citation: linked={len(res.linked)}, "
        f"orphans={len(res.orphans)}, ambiguous={len(res.ambiguous)}"
    )


@pytest.mark.parametrize(
    "single_org",
    [
        "Food and Agriculture Organization",
        "Department of Science and Technology",
        "National Economic and Development Authority",
        "Department of Information and Communications Technology",
        "Department of Public Works and Highways",
        "Department of Trade and Industry",
        "Commission on Higher Education",
    ],
)
def test_single_corporate_author_with_internal_and_never_split(single_org):
    """Adversarial stress test: single corporate entity with internal 'and' must NEVER be split."""
    splits = _split_authors(single_org)
    assert splits == [single_org], f"Improperly split single corporate author: {splits}"


@pytest.mark.parametrize("join", [" and ", " & "])
@pytest.mark.parametrize("reverse", [False, True])
def test_joint_corporate_authors_use_complete_reference_evidence(join, reverse):
    authors = ["World Health Organization", "Food and Agriculture Organization"]
    if reverse:
        authors.reverse()
    expression = join.join(authors)
    # Standalone text cannot establish whether a conjunction separates authors.
    assert _split_authors(expression) == [expression]
    reference = MockRef(0, expression + ". (2024). Health report.", authors, 2024)
    result = cross_match([MockInText([expression], "2024")], [reference])
    assert len(result.linked) == 1
    assert result.linked[0][1] == [reference]
    assert not result.orphans and not result.ambiguous


def test_unknown_corporate_boundaries_are_not_recovered_from_a_suffix():
    expression = "Office of Food and Agriculture Organization"
    assert _split_authors(expression) == [expression]
    reference = MockRef(
        0,
        "Food and Agriculture Organization. (2024). Report.",
        ["Food and Agriculture Organization"],
        2024,
    )
    result = cross_match([MockInText([expression], "2024")], [reference])
    assert not result.linked and not result.orphans
    assert len(result.ambiguous) == 1
    assert not result.confidently_uncited


def test_competing_complete_corporate_interpretations_remain_ambiguous():
    expression = "Institute of Research and Development Agency"
    assert _split_authors(expression) == [expression]
    references = [
        MockRef(0, expression + ". (2024). One source.", [expression], 2024),
        MockRef(
            1,
            expression + ". (2024). Another source.",
            ["Institute of Research", "Development Agency"],
            2024,
        ),
    ]
    for candidates in (references, list(reversed(references))):
        result = cross_match([MockInText([expression], "2024")], candidates)
        assert not result.linked and not result.orphans
        assert len(result.ambiguous) == 1
        assert {ref.order_index for ref in result.possibly_cited} == {0, 1}
        assert not result.confidently_uncited


def test_cross_match_three_joint_corporate_authors():
    """Verify cross_match links citation with 3 joint corporate authors without commas."""
    authors = [
        "Food and Agriculture Organization",
        "World Health Organization",
        "International Atomic Energy Agency",
    ]
    ref_title = (
        "Food and Agriculture Organization, World Health Organization & "
        "International Atomic Energy Agency. (2024). Standards."
    )
    ref = MockRef(0, ref_title, authors, 2024)
    raw_cite = (
        "Food and Agriculture Organization and World Health Organization "
        "and International Atomic Energy Agency"
    )
    cite_surnames = _split_authors(raw_cite)
    cite = MockInText(surnames=cite_surnames, year="2024", raw_text=f"({raw_cite}, 2024)")
    result = cross_match([cite], [ref])
    assert len(result.linked) == 1, (
        f"Failed linking 3-way joint corporate citation: linked={len(result.linked)}, "
        f"orphans={len(result.orphans)}"
    )


# =============================================================================
# 3. Dotted Acronym Normalization Stress Tests
# =============================================================================


@pytest.mark.parametrize(
    "dotted_input, expected_tokens",
    [
        ("W.H.O.", {"who", "world health organization"}),
        ("w.h.o.", {"who", "world health organization"}),
        ("W. H. O.", {"who", "world health organization"}),
        ("D.O.S.T.", {"dost", "department of science and technology"}),
        ("C.H.E.D.", {"ched", "commission on higher education"}),
        ("P.S.A.", {"psa", "philippine statistics authority"}),
        ("D.I.C.T.", {"dict", "department of information and communications technology"}),
        ("N.E.D.A.", {"neda", "national economic and development authority"}),
        ("D.O.H.", {"doh", "department of health"}),
        ("D.P.W.H.", {"dpwh", "department of public works and highways"}),
        ("D.T.I.", {"dti", "department of trade and industry"}),
    ],
)
def test_dotted_acronym_aliases_normalization(dotted_input, expected_tokens):
    """Verify get_author_aliases normalizes dotted acronyms to known expansions."""
    aliases = get_author_aliases(dotted_input)
    for expected in expected_tokens:
        assert expected in aliases, (
            f"Expected alias '{expected}' not found in aliases for '{dotted_input}': {aliases}"
        )


@pytest.mark.parametrize(
    "dotted_acronym, full_org_name",
    [
        ("A.S.T.I.", "Advanced Science and Technology Institute"),
        ("N.T.C.", "National Telecommunications Commission"),
    ],
)
def test_dynamic_initialism_dotted_cross_match(dotted_acronym, full_org_name):
    """Verify dynamic initialism links unlisted organization with dotted acronym citation."""
    ref = MockRef(0, f"{full_org_name}. (2023). Manual.", [full_org_name], 2023)
    cite = MockInText(
        surnames=[dotted_acronym],
        year="2023",
        raw_text=f"({dotted_acronym}, 2023)",
    )
    res = cross_match([cite], [ref])
    assert len(res.linked) == 1, (
        f"Failed linking dotted dynamic initialism '{dotted_acronym}' <-> '{full_org_name}'"
    )


def test_in_text_extraction_dotted_acronym():
    """Verify in-text citation extractor extracts parenthetical and narrative dotted acronyms."""
    text_paren = "Clinical guidelines are established (W.H.O., 2024)."
    cites_paren = extract_in_text_citations([_block(text_paren)])
    assert len(cites_paren) == 1
    assert cites_paren[0].surnames == ["W.H.O."]
    assert cites_paren[0].year == "2024"

    text_narr = "According to W.H.O. (2024), health metrics improved."
    cites_narr = extract_in_text_citations([_block(text_narr)])
    assert len(cites_narr) == 1
    assert cites_narr[0].surnames == ["W.H.O."]
    assert cites_narr[0].year == "2024"
    assert cites_narr[0].is_narrative is True


# =============================================================================
# 4. DataCite Corrupt / Non-Dict Schema Response Handling Stress Tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corrupt_payload",
    [
        [],  # list instead of dict
        "invalid string response",  # raw string
        12345,  # integer
        {},  # empty dict missing "data"
        {"data": None},  # data is null
        {"data": []},  # data is list
        {"data": "unexpected text"},  # data is string
        {"data": {"attributes": None}},  # attributes is null
        {"data": {"attributes": []}},  # attributes is list
        {"data": {"attributes": {}}},  # empty attributes missing titles/type/doi
        {
            "data": {
                "type": "wrong_type",
                "id": "10.5281/zenodo.1",
                "attributes": {"titles": [{"title": "Title"}]},
            }
        },  # type != 'dois'
        {
            "data": {
                "type": "dois",
                "id": "10.5281/zenodo.1",
                "attributes": {"titles": []},
            }
        },  # titles is empty list
        {
            "data": {
                "type": "dois",
                "id": "10.5281/zenodo.1",
                "attributes": {"titles": [{"title": ""}]},
            }
        },  # title is empty string
        {
            "data": {
                "type": "dois",
                "id": "10.5281/zenodo.1",
                "attributes": {"titles": [{"title": None}]},
            }
        },  # title is null
        {
            "data": {
                "type": "dois",
                "id": "10.5281/zenodo.1",
                "attributes": {"titles": "not_a_list"},
            }
        },  # titles is not a list
        {
            "data": {
                "type": "dois",
                "id": 99999,
                "attributes": {"titles": [{"title": "Title"}]},
            }
        },  # id is non-string and attributes.doi missing
    ],
)
async def test_datacite_corrupt_payload_raises_api_down(corrupt_payload):
    """Stress test: DataCite returning malformed/corrupt schema must raise ApiDownError."""
    client = AsyncMock()
    settings = get_settings()
    with (
        patch("app.external.datacite.get_json", AsyncMock(return_value=corrupt_payload)),
        pytest.raises(ApiDownError),
    ):
        await datacite_client.lookup_doi(client, "10.5281/zenodo.123456", settings)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "network_error",
    [
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectTimeout("connection timed out"),
        httpx.ConnectError("connection refused"),
    ],
)
async def test_datacite_network_transport_errors_raise_api_down(network_error):
    """Stress test: DataCite network transport failures raise ApiDownError."""
    client = AsyncMock()
    settings = get_settings()
    with (
        patch(
            "app.external.datacite.get_json",
            AsyncMock(side_effect=ApiDownError(str(network_error))),
        ),
        pytest.raises(ApiDownError),
    ):
        await datacite_client.lookup_doi(client, "10.5281/zenodo.123456", settings)


@pytest.mark.asyncio
async def test_verify_citation_datacite_schema_corruption_yields_api_down():
    """Stress test: verify_citation marks DOI as api_down on schema corruption."""
    doi = "10.5281/zenodo.9999999"
    c = Citation(
        order_index=0,
        raw_text=f"Smith, J. (2020). Dataset. doi:{doi}",
        authors=["Smith, J."],
        title="Dataset",
        year=2020,
        doi=doi,
        parse_status=CitationParseStatus.parsed,
    )

    def handler(request):
        if request.url.host == "api.crossref.org":
            return httpx.Response(404)
        return httpx.Response(200, json={"data": [{"unexpected": "structure"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verdict = await verify_citation(None, client, c, settings=Settings(external_max_retries=0))
    assert verdict.kind == VerdictKind.api_down, (
        f"Expected VerdictKind.api_down on schema corruption, got {verdict.kind}"
    )


@pytest.mark.asyncio
async def test_verify_citation_datacite_zenodo_success_confirms_existence():
    """Verify verify_citation confirms existence on Crossref 404 and DataCite 200 Zenodo."""
    doi = "10.5281/zenodo.123456"
    c = Citation(
        order_index=0,
        raw_text=f"Smith, J. (2024). Open Dataset. doi:{doi}",
        authors=["Smith, J."],
        title="Open Dataset",
        year=2024,
        doi=doi,
        parse_status=CitationParseStatus.parsed,
    )

    datacite_valid_payload = {
        "data": {
            "id": doi,
            "type": "dois",
            "attributes": {
                "doi": doi,
                "titles": [{"title": "Open Dataset"}],
                "publicationYear": 2024,
                "creators": [{"name": "Smith, J.", "familyName": "Smith"}],
                "url": "https://zenodo.org/records/123456",
            },
        }
    }

    def handler(request):
        if request.url.host == "api.crossref.org":
            return httpx.Response(404)
        return httpx.Response(200, json=datacite_valid_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verdict = await verify_citation(None, client, c, settings=Settings(external_max_retries=0))
    assert verdict.kind == VerdictKind.existence_confirmed
    assert verdict.result is not None
    assert verdict.result.provider == "datacite"
    assert verdict.result.title == "Open Dataset"
    assert verdict.result.url == "https://zenodo.org/records/123456"


@pytest.mark.asyncio
async def test_verify_citation_datacite_404_returns_not_found():
    """Verify verify_citation returns not_found when both Crossref and DataCite 404."""
    doi = "10.5281/zenodo.0000000"
    c = Citation(
        order_index=0,
        raw_text=f"Smith, J. (2024). Missing Dataset. doi:{doi}",
        authors=["Smith, J."],
        title="Missing Dataset",
        year=2024,
        doi=doi,
        parse_status=CitationParseStatus.parsed,
    )

    def handler(request):
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verdict = await verify_citation(None, client, c, settings=Settings(external_max_retries=0))
    assert verdict.kind == VerdictKind.not_found
    assert verdict.result.provider == "datacite"
    assert verdict.result.attempted_providers == ["crossref", "datacite"]
