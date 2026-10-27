"""Milestone 2 Adversarial Challenge Test Suite — Citation Integrity Stress Tests.

Targets:
1. Citations with n.d., (n.d.), n. d. punctuation variations.
2. Corporate authors with multiple "and" / "&" (e.g.,
   "Food and Agriculture Organization and World Health Organization").
3. Acronym matching across various cases and multi-word initialisms.
4. Numbered citation syntax: single [1], comma-separated [1, 3, 5], range [1]-[4].
5. DataCite client behavior on 404, valid DOI, rate limiting, and timeout.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from app.checks.citations.cross_match import (
    cross_match,
    parse_numeric_citation_numbers,
)
from app.checks.citations.extract import (
    _split_authors,
    extract_in_text_citations,
)
from app.checks.citations.verify import _lookup_doi_chain
from app.config import get_settings
from app.errors import ApiDownError
from app.external import datacite as datacite_client
from app.external.schemas import ExternalWorkRecord, VerificationResult
from app.ingest.references import parse_reference
from app.ingest.schemas import TextBlock
from app.models.enums import CitationParseStatus


@dataclass
class FakeRef:
    order_index: int
    raw_text: str
    authors: list[str] | None
    year: int | str | None


@dataclass(frozen=True)
class FakeInText:
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


# =============================================================================
# 1. Citations with n.d., (n.d.), n. d. Punctuation Variations
# =============================================================================


def test_reference_nd_date_canonical_and_uppercase():
    """Verify canonical (n.d.) and uppercase (N.D.) are accepted in reference parsing."""
    raw_lower = "World Health Organization. (n.d.). Emergency guidance. WHO."
    draft_lower = parse_reference(raw_lower, 0)
    assert draft_lower.parse_status == CitationParseStatus.parsed
    assert draft_lower.year == "n.d."
    assert draft_lower.authors == ["World Health Organization"]

    raw_upper = "World Health Organization. (N.D.). Emergency guidance. WHO."
    draft_upper = parse_reference(raw_upper, 1)
    assert draft_upper.parse_status == CitationParseStatus.parsed
    assert draft_upper.year == "n.d."


def test_reference_nd_date_spaced_variation():
    """Adversarial stress test: APA reference with spaced '(n. d.)' notation."""
    raw = "Smith, J. (n. d.). Algorithmic foundations. Academic Press."
    draft = parse_reference(raw, 0)
    assert draft.parse_status == CitationParseStatus.parsed, (
        f"Failed parsing spaced (n. d.): status={draft.parse_status}"
    )
    assert draft.year == "n.d."
    assert draft.authors == ["Smith, J."]


def test_in_text_parenthetical_nd_spaced():
    """Adversarial stress test: in-text parenthetical citation with spaced 'n. d.'."""
    text = "Guidelines exist (World Health Organization, n. d.)."
    cites = extract_in_text_citations([_block(text)])
    assert len(cites) == 1, f"Expected 1 citation for (WHO, n. d.), found {len(cites)}"
    assert cites[0].year == "n.d."
    assert cites[0].surnames == ["World Health Organization"]


def test_in_text_narrative_nd_spaced():
    """Adversarial stress test: in-text narrative citation with spaced '(n. d.)'."""
    text = "FAO (n. d.) provides food security recommendations."
    cites = extract_in_text_citations([_block(text)])
    assert len(cites) == 1, f"Expected 1 citation for FAO (n. d.), found {len(cites)}"
    assert cites[0].year == "n.d."
    assert cites[0].surnames == ["FAO"]
    assert cites[0].is_narrative is True


# =============================================================================
# 2. Corporate Authors with Multiple "and" / "&"
# =============================================================================


def test_single_corporate_author_internal_and_preserved():
    """Verify single corporate authors with internal 'and' are preserved as one name."""
    assert _split_authors("Food and Agriculture Organization") == [
        "Food and Agriculture Organization"
    ]
    assert _split_authors("Department of Science and Technology") == [
        "Department of Science and Technology"
    ]


def test_corporate_authors_joined_by_and_no_comma():
    """Adversarial stress test: Two corporate authors joined by 'and' without comma.

    Target: 'Food and Agriculture Organization and World Health Organization'
    Must be split into two distinct organization names, not retained as a single combined string.
    """
    raw = "Food and Agriculture Organization and World Health Organization"
    surnames = _split_authors(raw)
    assert surnames == [
        "Food and Agriculture Organization",
        "World Health Organization",
    ], f"Failed splitting two corporate authors without comma: got {surnames}"


def test_corporate_authors_joined_by_ampersand_no_comma():
    """Adversarial stress test: Two corporate authors joined by '&' without comma.

    Target: 'Food and Agriculture Organization & World Health Organization'
    """
    raw = "Food and Agriculture Organization & World Health Organization"
    surnames = _split_authors(raw)
    assert surnames == [
        "Food and Agriculture Organization",
        "World Health Organization",
    ], f"Failed splitting two corporate authors with '&': got {surnames}"


def test_corporate_authors_with_comma_and_split():
    """Verify corporate authors separated by comma and 'and' split correctly."""
    raw = "Food and Agriculture Organization, and World Health Organization"
    surnames = _split_authors(raw)
    assert surnames == [
        "Food and Agriculture Organization",
        "World Health Organization",
    ]


def test_cross_match_joint_corporate_coauthored_reference():
    """Adversarial stress test: Cross-match citation of joint corporate publication."""
    ref = FakeRef(
        0,
        "Food and Agriculture Organization & World Health Organization. (2024). Joint Standards.",
        ["Food and Agriculture Organization", "World Health Organization"],
        2024,
    )
    surnames = _split_authors("Food and Agriculture Organization and World Health Organization")
    cite = FakeInText(
        surnames=surnames,
        year="2024",
        raw_text="(Food and Agriculture Organization and World Health Organization, 2024)",
    )
    result = cross_match([cite], [ref])
    assert len(result.linked) == 1, (
        f"Failed linking joint corporate citation: linked={len(result.linked)}, "
        f"orphans={len(result.orphans)}, ambiguous={len(result.ambiguous)}"
    )


# =============================================================================
# 3. Acronym Matching Across Various Cases and Multi-Word Initialisms
# =============================================================================


def test_acronym_case_insensitivity_and_aliases():
    """Verify known acronyms match regardless of casing."""
    cases = ["WHO", "who", "Who", "wHo"]
    ref = FakeRef(
        0,
        "World Health Organization. (2024). Global Report.",
        ["World Health Organization"],
        2024,
    )
    for acr in cases:
        cite = FakeInText(surnames=[acr], year="2024", raw_text=f"({acr}, 2024)")
        res = cross_match([cite], [ref])
        assert len(res.linked) == 1, f"Failed linking {acr} to World Health Organization"


def test_acronym_philippine_bodies():
    """Verify Philippine government and academic acronyms link correctly."""
    pairs = [
        ("CHED", "Commission on Higher Education"),
        ("DOST", "Department of Science and Technology"),
        ("PSA", "Philippine Statistics Authority"),
        ("DEPED", "Department of Education"),
        ("NEDA", "National Economic and Development Authority"),
        ("DOH", "Department of Health"),
        ("DICT", "Department of Information and Communications Technology"),
        ("DPWH", "Department of Public Works and Highways"),
        ("DTI", "Department of Trade and Industry"),
    ]
    for idx, (acr, full_name) in enumerate(pairs):
        ref = FakeRef(idx, f"{full_name}. (2023). Order.", [full_name], 2023)
        cite = FakeInText(surnames=[acr], year="2023", raw_text=f"({acr}, 2023)")
        res = cross_match([cite], [ref])
        assert len(res.linked) == 1, f"Failed linking {acr} <-> {full_name}"


def test_dynamic_multiword_initialisms():
    """Verify dynamic initialism generation for unlisted multi-word organizations."""
    unlisted_pairs = [
        ("ASTI", "Advanced Science and Technology Institute"),
        ("NTC", "National Telecommunications Commission"),
    ]
    for idx, (acr, full_name) in enumerate(unlisted_pairs):
        ref = FakeRef(idx, f"{full_name}. (2023). Bulletin.", [full_name], 2023)
        cite = FakeInText(surnames=[acr], year="2023", raw_text=f"({acr}, 2023)")
        res = cross_match([cite], [ref])
        assert len(res.linked) == 1, f"Failed linking dynamic initialism {acr} <-> {full_name}"


def test_cross_match_acronym_with_periods():
    """Adversarial stress test: Acronym formatted with periods (e.g. W.H.O.)."""
    ref = FakeRef(
        0,
        "World Health Organization. (2024). Report.",
        ["World Health Organization"],
        2024,
    )
    cite = FakeInText(surnames=["W.H.O."], year="2024", raw_text="(W.H.O., 2024)")
    res = cross_match([cite], [ref])
    assert len(res.linked) == 1, (
        f"Failed linking dotted acronym W.H.O.: linked={len(res.linked)}, "
        f"orphans={len(res.orphans)}"
    )


# =============================================================================
# 4. Numbered Citation Syntax
# =============================================================================


def test_numbered_syntax_single_and_comma_separated():
    """Verify single and comma-separated numbered citations."""
    cite_single = FakeInText(surnames=[], year="", raw_text="[1]")
    assert parse_numeric_citation_numbers(cite_single) == [1]

    cite_spaced = FakeInText(surnames=[], year="", raw_text="[1, 3, 5]")
    assert parse_numeric_citation_numbers(cite_spaced) == [1, 3, 5]

    cite_unspaced = FakeInText(surnames=[], year="", raw_text="[1,3,5]")
    assert parse_numeric_citation_numbers(cite_unspaced) == [1, 3, 5]

    cite_semicolon = FakeInText(surnames=[], year="", raw_text="[1; 3; 5]")
    assert parse_numeric_citation_numbers(cite_semicolon) == [1, 3, 5]


def test_numbered_syntax_ranges_and_dashes():
    """Verify bracketed ranges with hyphens, en-dashes, and em-dashes."""
    cite_hyphen = FakeInText(surnames=[], year="", raw_text="[1]-[4]")
    assert parse_numeric_citation_numbers(cite_hyphen) == [1, 2, 3, 4]

    cite_single_bracket_range = FakeInText(surnames=[], year="", raw_text="[1-4]")
    assert parse_numeric_citation_numbers(cite_single_bracket_range) == [1, 2, 3, 4]

    cite_endash = FakeInText(surnames=[], year="", raw_text="[1–4]")
    assert parse_numeric_citation_numbers(cite_endash) == [1, 2, 3, 4]

    cite_endash_separate = FakeInText(surnames=[], year="", raw_text="[1]–[4]")
    assert parse_numeric_citation_numbers(cite_endash_separate) == [1, 2, 3, 4]

    cite_emdash = FakeInText(surnames=[], year="", raw_text="[1—4]")
    assert parse_numeric_citation_numbers(cite_emdash) == [1, 2, 3, 4]


def test_numbered_syntax_mixed_and_edge_cases():
    """Verify mixed comma and range citations and invalid syntax rejection."""
    cite_mixed = FakeInText(surnames=[], year="", raw_text="[1, 3-5, 8]")
    assert parse_numeric_citation_numbers(cite_mixed) == [1, 3, 4, 5, 8]

    # Non-numeric markers must return None
    assert (
        parse_numeric_citation_numbers(FakeInText(surnames=[], year="", raw_text="[Note 1]"))
        is None
    )
    assert parse_numeric_citation_numbers(FakeInText(surnames=[], year="", raw_text="[1a]")) is None
    assert (
        parse_numeric_citation_numbers(
            FakeInText(surnames=["Smith"], year="2020", raw_text="(Smith, 2020)")
        )
        is None
    )

    # Inverted ranges and unbounded ranges must return None
    assert (
        parse_numeric_citation_numbers(FakeInText(surnames=[], year="", raw_text="[10-5]"))
        is None
    )
    assert (
        parse_numeric_citation_numbers(FakeInText(surnames=[], year="", raw_text="[1-100]"))
        is None
    )


def test_cross_match_numbered_references_linking_and_orphans():
    """Verify numbered citations link to explicit reference labels and detect orphans."""
    ref1 = FakeRef(0, "[1] Alpha, A. (2020). First.", None, 2020)
    ref2 = FakeRef(1, "[2] Beta, B. (2021). Second.", None, 2021)
    ref3 = FakeRef(2, "[3] Gamma, C. (2022). Third.", None, 2022)
    refs = [ref1, ref2, ref3]

    cite_linked = FakeInText(surnames=["[1, 3]"], year="", raw_text="[1, 3]")
    res = cross_match([cite_linked], refs)
    assert len(res.linked) == 1
    assert res.linked[0][1] == [ref1, ref3]
    assert res.uncited == [ref2]
    assert res.orphans == []

    # Numbered orphan
    cite_orphan = FakeInText(surnames=["[99]"], year="", raw_text="[99]")
    res_orphan = cross_match([cite_orphan], refs)
    assert len(res_orphan.orphans) == 1
    assert res_orphan.linked == []


# =============================================================================
# 5. DataCite Client Behavior on 404, Valid DOI, Rate Limiting, and Timeout
# =============================================================================


@pytest.mark.asyncio
async def test_datacite_lookup_doi_valid_metadata():
    """Verify DataCite client parses valid 200 OK metadata record."""
    client = AsyncMock()
    settings = get_settings()
    payload = {
        "data": {
            "id": "10.5281/zenodo.123456",
            "type": "dois",
            "attributes": {
                "doi": "10.5281/zenodo.123456",
                "titles": [{"title": "Open Source Research Dataset"}],
                "url": "https://zenodo.org/records/123456",
            },
        }
    }
    with patch("app.external.datacite.get_json", AsyncMock(return_value=payload)):
        res = await datacite_client.lookup_doi(client, "10.5281/zenodo.123456", settings)
        assert isinstance(res, ExternalWorkRecord)
        assert res.found is True
        assert res.provider == "datacite"
        assert res.title == "Open Source Research Dataset"
        assert res.url == "https://zenodo.org/records/123456"
        assert res.content_checkable is False


@pytest.mark.asyncio
async def test_datacite_lookup_doi_404_not_found():
    """Verify DataCite returns found=False on 404 without raising error."""
    client = AsyncMock()
    settings = get_settings()
    with patch("app.external.datacite.get_json", AsyncMock(return_value=None)):
        res = await datacite_client.lookup_doi(client, "10.5281/zenodo.999999", settings)
        assert isinstance(res, VerificationResult)
        assert res.found is False
        assert res.provider == "datacite"


@pytest.mark.asyncio
async def test_datacite_lookup_doi_rate_limiting_429():
    """Adversarial stress test: DataCite HTTP 429 rate limit raises ApiDownError."""
    client = AsyncMock()
    settings = get_settings()
    err_msg = "datacite lookup could not be completed after retries."
    with (
        patch("app.external.datacite.get_json", AsyncMock(side_effect=ApiDownError(err_msg))),
        pytest.raises(ApiDownError, match="datacite lookup could not be completed"),
    ):
        await datacite_client.lookup_doi(client, "10.5281/zenodo.123456", settings)


@pytest.mark.asyncio
async def test_datacite_lookup_doi_timeout():
    """Adversarial stress test: DataCite timeout raises ApiDownError."""
    client = AsyncMock()
    settings = get_settings()
    with patch(
        "app.external.datacite.get_json",
        AsyncMock(side_effect=ApiDownError("datacite lookup timed out after retries.")),
    ), pytest.raises(ApiDownError, match="datacite lookup timed out"):
        await datacite_client.lookup_doi(client, "10.5281/zenodo.123456", settings)


@pytest.mark.asyncio
async def test_datacite_lookup_doi_malformed_json_schema():
    """Adversarial stress test: DataCite returning unexpected non-dict data raises ApiDownError."""
    client = AsyncMock()
    settings = get_settings()
    with (
        patch("app.external.datacite.get_json", AsyncMock(return_value={"data": []})),
        pytest.raises(ApiDownError),
    ):
        await datacite_client.lookup_doi(client, "10.5281/zenodo.123456", settings)


@pytest.mark.asyncio
async def test_doi_chain_fallback_and_outage_handling():
    """Verify _lookup_doi_chain falls back to DataCite on Crossref 404,
    and raises ApiDownError on outage.
    """
    client = AsyncMock()
    settings = get_settings()

    # Fallback to DataCite
    crossref_404 = VerificationResult(found=False, provider="crossref")
    datacite_hit = VerificationResult(
        found=True, provider="datacite", title="Zenodo Dataset", url="https://zenodo.org/1"
    )
    with (
        patch("app.external.crossref.lookup_doi", AsyncMock(return_value=crossref_404)),
        patch("app.external.datacite.lookup_doi", AsyncMock(return_value=datacite_hit)),
    ):
        res = await _lookup_doi_chain(client, "10.5281/zenodo.1", settings)
        assert res.found is True
        assert res.provider == "datacite"
        assert res.title == "Zenodo Dataset"

    # Both registries return 404 -> clean not found
    datacite_404 = VerificationResult(found=False, provider="datacite")
    with (
        patch("app.external.crossref.lookup_doi", AsyncMock(return_value=crossref_404)),
        patch("app.external.datacite.lookup_doi", AsyncMock(return_value=datacite_404)),
    ):
        res = await _lookup_doi_chain(client, "10.5281/zenodo.unknown", settings)
        assert res.found is False
        assert "crossref" in res.attempted_providers
        assert "datacite" in res.attempted_providers

    # Registry outage -> raises ApiDownError
    datacite_down = ApiDownError("DataCite down")
    with (
        patch("app.external.crossref.lookup_doi", AsyncMock(return_value=crossref_404)),
        patch("app.external.datacite.lookup_doi", AsyncMock(side_effect=datacite_down)),
        pytest.raises(ApiDownError, match="At least one DOI registry could not be checked"),
    ):
        await _lookup_doi_chain(client, "10.5281/zenodo.1", settings)
